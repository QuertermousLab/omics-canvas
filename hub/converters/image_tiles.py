"""Tissue images (H&E, IHC, IF, ...) -> viewer image tiles aligned to the slide coordinates.

A source is an image (one or more resolution levels of an H x W x 3/4 RGB(A) or H x W grey array; numpy or
zarr) plus the affine transform from its full-resolution pixel (column, row) to slide coordinates. Several
sources (sections of one object laid out side by side) go into one channel. The layout is xenium_to_viewer.py's:
img_<c>.bin + img_<c>.idx (float64 [offset, length] per tile, levels from the first one up; a level-L tile
covers TILE * 2**L slide units). Brightfield images are WebP tiles with alpha, so the area outside the image
stays transparent; grey (fluorescence) images are JPEG like the Xenium morphology channels.
"""
import io
import math

import numpy as np

TILE = 512


class Source:
    def __init__(self, levels, affine, mode='rgb'):
        """levels: [(array, factor)], factor = level-0 pixels per pixel of that level (1 for level 0).
        affine: 2 x 3, slide (x, y) = affine @ (col, row, 1) of a level-0 pixel."""
        self.levels = sorted(levels, key=lambda lf: lf[1])
        self.affine = np.asarray(affine, dtype=np.float64).reshape(2, 3)
        self.mode = mode
        lin = self.affine[:, :2]
        self.inv = np.linalg.inv(lin)
        self.units_per_px = math.sqrt(abs(np.linalg.det(lin)))  # slide units per level-0 pixel

    def bounds(self):
        a = self.levels[0][0]
        h, w = a.shape[0], a.shape[1]
        corners = np.array([[0, 0, 1], [w, 0, 1], [0, h, 1], [w, h, 1]], dtype=np.float64)
        pts = corners @ self.affine.T
        return pts.min(axis=0), pts.max(axis=0)

    def level_for(self, units):
        """The coarsest level whose pixels are still no larger than `units` slide units."""
        best = self.levels[0]
        for arr, f in self.levels:
            if self.units_per_px * f <= units * 1.0001:
                best = (arr, f)
        return best


def as_uint8(a):
    a = np.asarray(a)
    if a.dtype == np.uint8:
        return a
    if np.issubdtype(a.dtype, np.floating):
        hi = 1.0 if np.nanmax(a) <= 1.0 else 255.0
        return np.clip(np.nan_to_num(a) * (255.0 / hi), 0, 255).astype(np.uint8)
    if a.dtype == np.uint16:
        return (a >> 8).astype(np.uint8)
    return np.clip(a, 0, 255).astype(np.uint8)


def stretch(a, lo_pct=0.5, hi_pct=99.5):
    """Percentile contrast stretch of a dim grey image (e.g. a dark brightfield scan stored as one channel)."""
    a = np.asarray(a, dtype=np.float32)
    lo, hi = np.percentile(a, [lo_pct, hi_pct])
    if hi <= lo:
        return as_uint8(a)
    return np.clip((a - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.uint8)


def _render(src, x0, y0, units, size):
    """RGBA (or L) PIL image of one output tile from one source, or None if it does not reach the tile."""
    from PIL import Image
    arr, f = src.level_for(units)
    h, w = arr.shape[0], arr.shape[1]
    # output pixel (u, v) -> slide (x0 + u * units, y0 + v * units) -> source level pixel
    lin = src.inv / f
    off = lin @ (np.array([x0, y0]) - src.affine[:, 2])
    m = lin * units
    corners = np.array([[0, 0], [size, 0], [0, size], [size, size]], dtype=np.float64) @ m.T + off
    c0, r0 = np.floor(corners.min(axis=0)).astype(int) - 2
    c1, r1 = np.ceil(corners.max(axis=0)).astype(int) + 2
    c0, r0, c1, r1 = max(c0, 0), max(r0, 0), min(c1, w), min(r1, h)
    if c1 <= c0 or r1 <= r0:
        return None
    win = as_uint8(arr[r0:r1, c0:c1])
    coeffs = (m[0, 0], m[0, 1], off[0] - c0, m[1, 0], m[1, 1], off[1] - r0)
    if src.mode == 'grey':
        g = win if win.ndim == 2 else win[..., :3].mean(axis=2).astype(np.uint8)
        return Image.fromarray(g, 'L').transform((size, size), Image.AFFINE, coeffs, resample=Image.BILINEAR, fillcolor=0)
    if win.ndim == 2:
        win = np.repeat(win[..., None], 3, axis=2)
    rgb = Image.fromarray(np.ascontiguousarray(win[..., :3]), 'RGB')
    alpha = Image.fromarray(np.ascontiguousarray(win[..., 3]) if win.shape[2] > 3 else np.full(win.shape[:2], 255, np.uint8), 'L')
    rgb = rgb.transform((size, size), Image.AFFINE, coeffs, resample=Image.BILINEAR)
    alpha = alpha.transform((size, size), Image.AFFINE, coeffs, resample=Image.BILINEAR, fillcolor=0)
    rgb.putalpha(alpha)
    return rgb


def pyramid(width, height, min_level):
    """Levels min_level.. until the slide fits one tile: [dict(level, width, height, rows, cols)]."""
    max_level = max(min_level, int(math.ceil(math.log2(max(width, height, 1) / TILE))))
    out = []
    for L in range(min_level, max_level + 1):
        w, h = int(math.ceil(width / 2 ** L)), int(math.ceil(height / 2 ** L))
        out.append(dict(level=L, width=w, height=h, rows=-(-h // TILE), cols=-(-w // TILE)))
    return out


def write_channel(sources, out, c, width, height, *, min_level=None, levels=None, quality=85, progress=None):
    """Tile `sources` (all one mode) into img_<c>.bin/.idx over the slide [0, width] x [0, height], on the given
    pyramid `levels` (e.g. the Xenium morphology channels') or a new one from `min_level`.
    Returns the level list for slide.json (level, width, height, rows, cols, start) and the bytes written."""
    from PIL import Image
    mode = sources[0].mode
    if levels is None:
        if min_level is None:  # about one image pixel per tile pixel at the finest level
            finest = min(s.units_per_px * s.levels[0][1] for s in sources)
            min_level = max(0, int(math.floor(math.log2(max(finest, 1e-9)) + 1e-6)))
        levels = pyramid(width, height, min_level)
    levels = [dict(lv) for lv in levels]
    entries, offset = [], 0
    total = sum(lv['rows'] * lv['cols'] for lv in levels)
    done = 0
    with open(out / f'img_{c}.bin', 'wb') as fh:
        for lv in levels:
            L, rows, cols = lv['level'], lv['rows'], lv['cols']
            units = 2.0 ** L
            lv['start'] = len(entries)
            for ty in range(rows):
                for tx in range(cols):
                    x0, y0 = tx * TILE * units, ty * TILE * units
                    tile = None
                    for s in sources:
                        part = _render(s, x0, y0, units, TILE)
                        if part is None:
                            continue
                        if tile is None:
                            tile = part
                        elif mode == 'grey':
                            tile = Image.fromarray(np.maximum(np.asarray(tile), np.asarray(part)), 'L')
                        else:
                            tile = Image.alpha_composite(tile, part)
                    done += 1
                    empty = tile is None or (np.asarray(tile).max() <= 2 if mode == 'grey' else tile.getchannel('A').getextrema()[1] == 0)
                    if empty:
                        entries.append((offset, 0))
                        continue
                    buf = io.BytesIO()
                    if mode == 'grey':
                        tile.save(buf, 'JPEG', quality=quality)
                    else:
                        tile.save(buf, 'WEBP', quality=quality, method=4)
                    data = buf.getvalue()
                    fh.write(data)
                    entries.append((offset, len(data)))
                    offset += len(data)
                    if progress and done % 50 == 0:
                        progress(done / max(1, total))
    np.asarray(entries, dtype=np.float64).tofile(out / f'img_{c}.idx')
    return levels, offset


class TiffLevel:
    """One level of a pyramidal (OME-)TIFF as a lazily read H x W [x C] array (channel-first stored images too)."""
    def __init__(self, path, level):
        import tifffile
        import zarr
        self.z = zarr.open(tifffile.imread(path, aszarr=True, level=level), mode='r')
        shape = self.z.shape
        self.chfirst = len(shape) == 3 and shape[0] in (3, 4) and shape[2] not in (3, 4)
        self.shape = (shape[1], shape[2], shape[0]) if self.chfirst else tuple(shape)

    def __getitem__(self, key):
        rows, cols = key
        if self.chfirst:
            return np.moveaxis(np.asarray(self.z[:, rows, cols]), 0, -1)
        return np.asarray(self.z[rows, cols])


def tiff_levels(path):
    """[(TiffLevel, factor)] for every level of a pyramidal TIFF."""
    import tifffile
    with tifffile.TiffFile(path) as t:
        n = len(t.series[0].levels)
    levels = [TiffLevel(path, k) for k in range(n)]
    w0 = levels[0].shape[1]
    return [(lv, w0 / lv.shape[1]) for lv in levels]


def add_images(out, slide, channels, *, progress=None, log=print):
    """channels: [dict(name, sources=[Source], visible)] -> img_<c> files + slide['image'] (slide.json is rewritten
    by the caller). Tiles cover the slide's own extent."""
    if not channels:
        return slide
    # one pyramid for all channels: the finest any of them needs
    finest = min(s.units_per_px * s.levels[0][1] for ch in channels for s in ch['sources'])
    min_level = max(0, int(math.floor(math.log2(max(finest, 1e-9)) + 1e-6)))
    first = None
    entries = []
    for c, ch in enumerate(channels):
        lv, size = write_channel(ch['sources'], out, c, slide['width'], slide['height'], min_level=min_level,
                                 progress=(lambda f, c=c: progress((c + f) / len(channels))) if progress else None)
        first = first or lv
        mode = ch['sources'][0].mode
        entries.append(dict(name=ch['name'], file=f'img_{c}', color=ch.get('color', '#ffffff'), visible=ch.get('visible', True),
                            window=[0, 255], mode=mode))
        log(f'  image {ch["name"]}: levels {lv[0]["level"]}-{lv[-1]["level"]}, {size / 1e6:.1f} MB')
    slide['image'] = dict(tileSize=TILE, format='mixed', levels=first, channels=entries)
    return slide
