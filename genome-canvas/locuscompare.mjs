// LocusCompare (Liu et al. 2019): a GWAS track against eQTLs of one gene in one region. The server
// (locuscompare.py, /api/locuscompare) matches the variants, picks the lead SNP and computes LD; this module draws
// LocusCompare's figure — the two studies' -log10 p against each other, and a LocusZoom panel for each, with genes —
// as SVG in the Genome Canvas palette, colored by LD as the GWAS tracks are.
import { LD_COLORS } from "./manhattan-style.mjs?v=20261007.2";

const STROKES = { reference: "#642477", missing: "#7f7f7f", low: "#28577e", lowMedium: "#287b91", medium: "#39743a", mediumHigh: "#a96e1e", high: "#922c29" };

export function ldClass(r2, isLead = false) {
  if (isLead) return "reference";
  if (!Number.isFinite(r2)) return "missing";
  if (r2 >= 0.8) return "high";
  if (r2 >= 0.6) return "mediumHigh";
  if (r2 >= 0.4) return "medium";
  if (r2 >= 0.2) return "lowMedium";
  return "low";
}

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

/** Round axis ticks over [lo, hi]. */
export function niceTicks(lo, hi, count = 5) {
  const span = hi - lo || 1;
  const raw = span / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? magnitude * 10;
  const ticks = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi + step * 1e-9; t += step) ticks.push(+t.toPrecision(12));
  return ticks;
}

const fmtP = (p) => (p == null ? "—" : p === 0 ? "< 1e-300" : p < 1e-3 ? p.toExponential(2) : p.toPrecision(3));

/** Genes in rows that do not overlap (labels included). */
export function packGenes(genes, x, minGap = 6) {
  const rows = [];
  const placed = [];
  for (const gene of [...genes].sort((a, b) => a.start - b.start)) {
    const left = x(gene.start);
    const right = Math.max(x(gene.end), left + 1);
    const labelEnd = (left + right) / 2 + gene.symbol.length * 3.3 + minGap;
    const labelStart = (left + right) / 2 - gene.symbol.length * 3.3 - minGap;
    let row = rows.findIndex((end) => end < Math.min(left, labelStart));
    if (row < 0) { row = rows.length; rows.push(0); }
    rows[row] = Math.max(right, labelEnd);
    placed.push({ gene, row, left, right });
  }
  return { placed, rows: rows.length };
}

/** The figure as SVG (a string), and where each point is (for hover and clicks). */
export function locusCompareSVG(data, options = {}) {
  const theme = options.theme || {};
  const ink = theme.panelText || "#272b2e";
  const muted = theme.panelMuted || "#6f7477";
  const grid = theme.grid || "rgba(104,112,116,.16)";
  const axis = theme.axisLine || "rgba(96,104,108,.34)";
  const accent = options.accent || "#4c7a86";
  const font = `font-family="Arial, Helvetica, sans-serif"`;  // figures are exported in Arial
  const snps = data.snps || [];
  const lead = snps.find((s) => s.id === data.lead) || null;
  const title1 = data.gwas?.label || "GWAS";
  const title2 = `${data.eqtl?.symbol || ""} eQTL`;

  // layout: the LocusCompare scatter on the left, the two LocusZoom panels and the genes on the right
  const W = 1080, top = 64, side = 340, left = 62, zx = left + side + 84, zw = W - zx - 24, zh = 136, gap = 24;
  const maxX = Math.max(1, ...snps.map((s) => s.logp1)) * 1.06;
  const maxY = Math.max(1, ...snps.map((s) => s.logp2)) * 1.06;
  const sx = (v) => left + (v / maxX) * side;
  const sy = (v) => top + side - (v / maxY) * side;
  const { start, end, chrom } = data.region;
  const gx = (pos) => zx + ((pos - start) / (end - start)) * zw;
  const genes = (data.genes || []).filter((g) => g.end >= start && g.start <= end);
  const packed = packGenes(genes, gx);
  const geneTop = top + 2 * zh + gap + 30;
  const rowH = 17;
  const H = Math.max(top + side + 58, geneTop + Math.max(1, packed.rows) * rowH + 40);
  const points = [];
  const order = snps.map((s, i) => i).sort((a, b) => (snps[a].r2 ?? -1) - (snps[b].r2 ?? -1));
  const mark = (s, cx, cy, i, small = false) => {
    const isLead = lead && s.id === lead.id;
    const kind = ldClass(s.r2, isLead);
    const r = isLead ? 6.5 : small ? 3.2 : 3.8;
    const fill = LD_COLORS[kind];
    if (i != null) points.push({ i, x: cx, y: cy, r: r + 2 });
    const shape = isLead
      ? `<path d="M${cx},${cy - r} L${cx + r},${cy} L${cx},${cy + r} L${cx - r},${cy} Z"`
      : `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${r}"`;
    return `${shape} fill="${fill}" fill-opacity="${isLead ? 1 : 0.85}" stroke="${STROKES[kind]}" stroke-width="${isLead ? 1.2 : 0.6}"/>`;
  };
  const leadLast = (list) => (lead ? [...list.filter((i) => snps[i].id !== lead.id), snps.indexOf(lead)] : list);

  let svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" ${font}>`;
  if (options.background) svg += `<rect width="${W}" height="${H}" fill="${options.background}"/>`;
  const head = `${title1} × ${data.eqtl?.tissueLabel || ""} eQTL · ${data.eqtl?.symbol || ""}`;
  svg += `<text x="${left - 40}" y="24" font-size="15" font-weight="700" fill="${ink}">${esc(head)}</text>`;
  const sub = [`${chrom}:${start.toLocaleString()}-${end.toLocaleString()}`, `${snps.length.toLocaleString()} shared variants`,
    data.ld?.available ? `LD: ${data.ld.label}` : `LD: ${data.ld?.reason || "not available"}`,
    data.eqtl?.significantOnly ? "eQTLs: significant pairs only" : "", data.gwas?.subset ? `GWAS group ${data.gwas.subset}` : ""].filter(Boolean).join(" · ");
  svg += `<text x="${left - 40}" y="42" font-size="11.5" fill="${muted}">${esc(sub)}</text>`;

  // ---- scatter
  for (const t of niceTicks(0, maxX)) {
    svg += `<line x1="${sx(t)}" x2="${sx(t)}" y1="${top}" y2="${top + side}" stroke="${grid}"/><text x="${sx(t)}" y="${top + side + 16}" font-size="10.5" text-anchor="middle" fill="${muted}">${t}</text>`;
  }
  for (const t of niceTicks(0, maxY)) {
    svg += `<line x1="${left}" x2="${left + side}" y1="${sy(t)}" y2="${sy(t)}" stroke="${grid}"/><text x="${left - 7}" y="${sy(t) + 3.5}" font-size="10.5" text-anchor="end" fill="${muted}">${t}</text>`;
  }
  svg += `<rect x="${left}" y="${top}" width="${side}" height="${side}" fill="none" stroke="${axis}"/>`;
  svg += `<text x="${left + side / 2}" y="${top + side + 36}" font-size="12" text-anchor="middle" fill="${ink}">${esc(title1)} −log₁₀(P)</text>`;
  svg += `<text transform="translate(${left - 38} ${top + side / 2}) rotate(-90)" font-size="12" text-anchor="middle" fill="${ink}">${esc(title2)} −log₁₀(P)</text>`;
  svg += `<g data-panel="scatter">${leadLast(order).map((i) => mark(snps[i], sx(snps[i].logp1), sy(snps[i].logp2), i)).join("")}</g>`;
  if (lead) svg += `<text x="${Math.min(sx(lead.logp1) + 9, left + side - 4)}" y="${sy(lead.logp2) - 8}" font-size="11.5" font-weight="700" text-anchor="${sx(lead.logp1) > left + side - 80 ? "end" : "start"}" fill="${ink}" paint-order="stroke" stroke="${theme.halo || "#fff"}" stroke-width="3">${esc(lead.rsid || lead.id)}</text>`;
  // r² legend (as LocusCompare's)
  const lx = left + 12, ly = top + 12;
  svg += `<rect x="${lx - 6}" y="${ly - 6}" width="52" height="${5 * 13 + 26}" rx="4" fill="${theme.panel || "rgba(255,255,255,.9)"}" stroke="${grid}"/>`;
  svg += `<text x="${lx}" y="${ly + 7}" font-size="10.5" font-weight="700" fill="${ink}">r²</text>`;
  ["high", "mediumHigh", "medium", "lowMedium", "low"].forEach((k, n) => {
    const y = ly + 14 + n * 13;
    svg += `<rect x="${lx}" y="${y}" width="11" height="11" fill="${LD_COLORS[k]}" stroke="${STROKES[k]}" stroke-width=".6"/><text x="${lx + 16}" y="${y + 9}" font-size="9.5" fill="${muted}">${["0.8", "0.6", "0.4", "0.2", "0"][n]}</text>`;
  });

  // ---- LocusZoom panels
  const panel = (key, y0, label, max) => {
    const py = (v) => y0 + zh - (v / max) * zh;
    let out = `<rect x="${zx}" y="${y0}" width="${zw}" height="${zh}" fill="none" stroke="${axis}"/>`;
    for (const t of niceTicks(0, max, 4)) {
      out += `<line x1="${zx}" x2="${zx + zw}" y1="${py(t)}" y2="${py(t)}" stroke="${grid}"/><text x="${zx - 6}" y="${py(t) + 3.5}" font-size="10" text-anchor="end" fill="${muted}">${t}</text>`;
    }
    out += `<text transform="translate(${zx - 34} ${y0 + zh / 2}) rotate(-90)" font-size="11" text-anchor="middle" fill="${muted}">−log₁₀P</text>`;
    out += `<text x="${zx + 8}" y="${y0 + 15}" font-size="11.5" font-weight="700" fill="${ink}" paint-order="stroke" stroke="${theme.halo || "#fff"}" stroke-width="3">${esc(label)}</text>`;
    if (lead) out += `<line x1="${gx(lead.pos)}" x2="${gx(lead.pos)}" y1="${y0}" y2="${y0 + zh}" stroke="${muted}" stroke-dasharray="3 3"/>`;
    out += `<g data-panel="${key}">${leadLast(order).map((i) => mark(snps[i], gx(snps[i].pos), py(snps[i][key]), i, true)).join("")}</g>`;
    return out;
  };
  svg += panel("logp1", top, title1, maxX);
  svg += panel("logp2", top + zh + gap, `${title2} · ${data.eqtl?.tissueLabel || ""}`, maxY);
  // x axis (Mb) under the panels
  const ax = top + 2 * zh + gap;
  for (const t of niceTicks(start / 1e6, end / 1e6, 6)) {
    svg += `<line x1="${gx(t * 1e6)}" x2="${gx(t * 1e6)}" y1="${ax}" y2="${ax + 4}" stroke="${axis}"/><text x="${gx(t * 1e6)}" y="${ax + 15}" font-size="10" text-anchor="middle" fill="${muted}">${t}</text>`;
  }
  svg += `<text x="${zx + zw}" y="${ax + 28}" font-size="10" text-anchor="end" fill="${muted}">${esc(chrom)} (Mb)</text>`;
  // genes
  for (const { gene, row, left: gl, right: gr } of packed.placed) {
    const y = geneTop + 12 + row * rowH;
    const chosen = gene.id === data.eqtl?.gene;
    const color = chosen ? accent : muted;
    const mid = (gl + gr) / 2;
    const arrow = gene.strand === "-" ? "‹" : "›";
    svg += `<g><title>${esc(`${gene.symbol} (${gene.id}) ${gene.strand} ${gene.start.toLocaleString()}-${gene.end.toLocaleString()}`)}</title>`
      + `<line x1="${Math.max(zx, gl)}" x2="${Math.min(zx + zw, gr)}" y1="${y}" y2="${y}" stroke="${color}" stroke-width="${chosen ? 3.2 : 2.2}"/>`
      + `<text x="${Math.max(zx + 10, Math.min(zx + zw - 10, mid))}" y="${y - 4}" font-size="9.5" text-anchor="middle" fill="${color}" font-weight="${chosen ? 700 : 500}" font-style="italic">${esc(gene.symbol)} ${arrow}</text></g>`;
  }
  svg += "</svg>";
  return { svg, points, width: W, height: H };
}

/** Tab-separated merged data, for the "Table" download. */
export function locusCompareTSV(data) {
  const head = ["variant", "rsid", "chrom", "position", "ref", "alt", "gwas_p", "gwas_neg_log10_p", "gwas_beta", "eqtl_gene", "eqtl_p",
    "eqtl_neg_log10_p", "eqtl_beta", "ld_r2", "is_lead"];
  const rows = (data.snps || []).map((s) => [s.id, s.rsid, data.region.chrom, s.pos, s.ref, s.alt, s.p1 ?? "", s.logp1, s.beta1 ?? "",
    data.eqtl.symbol, s.p2, s.logp2, s.beta2 ?? "", s.r2 ?? "", s.id === data.lead ? 1 : 0].join("\t"));
  return [head.join("\t"), ...rows].join("\n") + "\n";
}

export function snpTooltip(data, s) {
  const lead = s.id === data.lead;
  return `<b>${esc(s.rsid || s.id)}</b>${lead ? " · lead" : ""}<br>${esc(data.region.chrom)}:${s.pos.toLocaleString()} ${esc(s.ref)}/${esc(s.alt)}`
    + `<br>${esc(data.gwas.label)}: P ${fmtP(s.p1)}${s.beta1 != null ? ` · β ${(+s.beta1).toFixed(3)}` : ""}`
    + `<br>${esc(data.eqtl.symbol)} eQTL: P ${fmtP(s.p2)}${s.beta2 != null ? ` · β ${(+s.beta2).toFixed(3)}` : ""}`
    + `<br>r² ${Number.isFinite(s.r2) ? s.r2.toFixed(2) : "—"}${lead ? "" : "<br><small>Click: use as the lead SNP</small>"}`;
}
