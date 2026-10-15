#!/usr/bin/env python3
"""Server settings of an Omics Canvas deployment (run as ./vizhubctl configure).

Reads deploy/site.env (or the THOR defaults in deploy/site.env.example), asks for each setting (Enter keeps it) or
takes them as options, writes deploy/site.env and generates from the templates:
  gateway/apache.conf                      from gateway/apache.conf.in
  deploy/generated/systemd/*.service       from deploy/systemd/*.service.in (linked by ./vizhubctl install)
  hub/config.json                          host, port, publicUrl, workspaceApi, Genome Canvas health (other keys kept)

  ./vizhubctl configure                                   ask, with the current values as defaults
  ./vizhubctl configure --yes                             regenerate from deploy/site.env without asking
  ./vizhubctl configure --listen 127.0.0.1 --port 9892 --public-url http://localhost:9892 --no-jupyter --yes
"""
import argparse
import getpass
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'deploy' / 'site.env'
EXAMPLE = ROOT / 'deploy' / 'site.env.example'
UNITS = ['vizhub-hub.service', 'genome-canvas.service', 'vizhub-gateway.service']
GENERATED = ROOT / 'deploy' / 'generated' / 'systemd'

# (key, option, question) in the order they are asked
SETTINGS = [
    ('VIZHUB_LISTEN', 'listen', 'IP the gateway listens on (0.0.0.0 = all interfaces, 127.0.0.1 = only through port forwarding)'),
    ('VIZHUB_PORT', 'port', 'Gateway port'),
    ('VIZHUB_PUBLIC_URL', 'public-url', 'Address people open in the browser (behind port forwarding e.g. http://localhost:<port>)'),
    ('VIZHUB_SERVER_NAME', 'server-name', 'Server name (host name or IP)'),
    ('VIZHUB_HUB_PORT', 'hub-port', 'Hub backend port (127.0.0.1)'),
    ('VIZHUB_CANVAS_PORT', 'canvas-port', 'Genome Canvas port (127.0.0.1)'),
    ('VIZHUB_JUPYTER_PORT', 'jupyter-port', 'JupyterLab port behind the gateway (empty: none)'),
    ('VIZHUB_JUPYTER_BIN', 'jupyter-bin', 'JupyterLab executable'),
    ('VIZHUB_SSH_HOST', 'ssh-host', 'SSH host for port forwarding from outside (empty: none)'),
    ('VIZHUB_SSH_PORT', 'ssh-port', 'SSH port'),
    ('VIZHUB_DATA_ROOTS', 'data-roots', 'Data folders the data pages browse (colon-separated)'),
    ('VIZHUB_APACHE_BIN', 'apache-bin', 'Apache executable'),
    ('VIZHUB_APACHE_MODULES', 'apache-modules', 'Apache modules folder'),
]
PORTS = {'VIZHUB_PORT', 'VIZHUB_HUB_PORT', 'VIZHUB_CANVAS_PORT', 'VIZHUB_SSH_PORT'}


def read_env(path):
    values = {}
    for line in path.read_text().splitlines():
        m = re.match(r'^\s*([A-Z_]+)=(.*)$', line)
        if m:
            values[m.group(1)] = m.group(2).strip().strip('"\'')
    return values


def write_env(values):
    """deploy/site.env: the example's comments and order, with these values."""
    out = []
    for line in EXAMPLE.read_text().splitlines():
        m = re.match(r'^\s*([A-Z_]+)=', line)
        out.append(f'{m.group(1)}={values.get(m.group(1), "")}' if m else line)
    SITE.write_text('\n'.join(out) + '\n')


def valid(key, value):
    if key in PORTS:
        return value.isdigit() and 0 < int(value) < 65536
    if key == 'VIZHUB_JUPYTER_PORT':
        return value == '' or (value.isdigit() and 0 < int(value) < 65536)
    if key == 'VIZHUB_LISTEN':
        return bool(re.fullmatch(r'[0-9.]+|\[?[0-9a-fA-F:]+\]?|\*', value))
    if key == 'VIZHUB_PUBLIC_URL':
        return bool(re.match(r'^https?://[^/\s]+$', value.rstrip('/')))
    return True


def render(template, values, out):
    jupyter = bool(values['VIZHUB_JUPYTER_PORT'])
    text = template.read_text()
    text = text.replace('@jupyter-unit@', 'jupyter-lab.service ' if jupyter else '')
    text = text.replace('@jupyter-label@', ' and JupyterLab' if jupyter else '')
    lines = []
    for line in text.splitlines():
        if line.startswith('@jupyter@'):
            if not jupyter:
                continue
            line = line[len('@jupyter@'):]
        lines.append(line)
    text = '\n'.join(lines) + '\n'
    for key, value in values.items():
        text = text.replace(f'@{key}@', value)
    left = re.findall(r'@[A-Z_]+@', text)
    if left:
        raise SystemExit(f'{template}: no value for {", ".join(sorted(set(left)))}')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)


def update_hub_config(values):
    path = ROOT / 'hub' / 'config.json'
    created = not path.exists()
    config = json.loads((path if path.exists() else ROOT / 'hub' / 'config.example.json').read_text())
    canvas = values['VIZHUB_CANVAS_PORT']
    config.update(host='127.0.0.1', port=int(values['VIZHUB_HUB_PORT']), publicUrl=values['VIZHUB_PUBLIC_URL'].rstrip('/'),
                  workspaceApi=f'http://127.0.0.1:{canvas}/api/workspaces')
    services = [s for s in config.get('services') or [] if s.get('id') != 'genome-canvas']
    config['services'] = [dict(id='genome-canvas', name='Genome Canvas', url='/genome-canvas/', health=f'http://127.0.0.1:{canvas}/api/health')] + services
    if created:
        roots = [r for r in values['VIZHUB_DATA_ROOTS'].split(':') if r]
        config['roots'] = [dict(label=r if r != '/nfs' else 'Shared NFS', path=r) for r in roots]
    path.write_text(json.dumps(config, indent=2) + '\n')
    return created


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for key, option, question in SETTINGS:
        ap.add_argument(f'--{option}', dest=key, help=question)
    ap.add_argument('--no-jupyter', action='store_true', help='no JupyterLab behind the gateway')
    ap.add_argument('--yes', '-y', action='store_true', help='do not ask; keep the current values for what is not given')
    a = ap.parse_args()

    values = read_env(EXAMPLE)
    if SITE.exists():
        values.update(read_env(SITE))
    values.update({k: v for k, v in vars(a).items() if k.startswith('VIZHUB_') and v is not None})
    if a.no_jupyter:
        values['VIZHUB_JUPYTER_PORT'] = ''
    ask = not a.yes and sys.stdin.isatty()
    if ask:
        print('Omics Canvas server settings — Enter keeps the value in [brackets], - leaves it empty.\n')
    for key, option, question in SETTINGS:
        if ask and getattr(a, key) is None and not (a.no_jupyter and key == 'VIZHUB_JUPYTER_PORT'):
            while True:
                answer = input(f'{question}\n  [{values.get(key, "")}] ').strip()
                value = values.get(key, '') if answer == '' else ('' if answer == '-' else answer)
                if valid(key, value):
                    values[key] = value
                    break
                print('  not valid, try again')
        elif not valid(key, values.get(key, '')):
            raise SystemExit(f'{key}={values.get(key, "")!r} is not valid (--{option})')
    values['VIZHUB_PUBLIC_URL'] = values['VIZHUB_PUBLIC_URL'].rstrip('/')
    used = [values[k] for k in ('VIZHUB_PORT', 'VIZHUB_HUB_PORT', 'VIZHUB_CANVAS_PORT', 'VIZHUB_JUPYTER_PORT') if values[k]]
    if len(set(used)) != len(used):
        raise SystemExit(f'the gateway, hub, Genome Canvas and JupyterLab need different ports: {", ".join(used)}')

    write_env(values)
    context = dict(values, VIZHUB_DIR=str(ROOT), VIZHUB_USER=getpass.getuser())
    render(ROOT / 'gateway' / 'apache.conf.in', context, ROOT / 'gateway' / 'apache.conf')
    for unit in UNITS:
        render(ROOT / 'deploy' / 'systemd' / f'{unit}.in', context, GENERATED / unit)
    dropin = GENERATED / 'jupyter-lab.service.d' / 'vizhub-gateway.conf'
    if values['VIZHUB_JUPYTER_PORT']:
        render(ROOT / 'deploy' / 'systemd' / 'jupyter-lab.service.d' / 'vizhub-gateway.conf.in', context, dropin)
    elif dropin.exists():
        dropin.unlink()
    created = update_hub_config(values)

    print(f'Wrote deploy/site.env, gateway/apache.conf, deploy/generated/systemd/ and hub/config.json{" (new)" if created else ""}.')
    print('Next: ./vizhubctl install (first time, or after changing ports) and ./vizhubctl restart — check the running')
    print('conversions first (see AGENTS.md). ./vizhubctl status shows the addresses and the port forwarding command.')


if __name__ == '__main__':
    main()
