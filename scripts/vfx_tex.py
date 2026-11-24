"""VFX texture tool. Standard library only, so it runs on Unreal's bundled Python:

  "<UE>/Engine/Binaries/ThirdParty/Python3/Win64/python.exe" vfx_tex.py <command> ...

Commands
  info  FILE...                       size and channel layout of PNGs
  pack  OUT.png IN... [--cols N] [--cell PX] [--luma-alpha] [--pad]
        Pack same-sized PNGs into one grid atlas (SubUV sheet). Row-major, left to right, top to
        bottom, which is the order Niagara's SubImageIndex counts in. --cell downscales each
        input to PX (integer factors only). --luma-alpha replaces alpha with brightness, for
        art drawn on a black background. Prints the grid to put in the renderer's SubImageSize.
  shape KIND OUT.png [--size N]       generate a starter texture: glow | ring | spark | noise | band
        glow/ring/spark are white with the shape in alpha (sprites). noise is tiling greyscale
        in RGB (import it as kind "tile" and scroll it in a panner material). band is a soft
        line across V in RGB, the Mask for ribbons and beams.

Reads 8-bit non-interlaced PNG (grey, grey+alpha, RGB, RGBA, palette). Writes 8-bit RGBA.
"""
import math
import struct
import sys
import zlib


# ----------------------------------------------------------------------------- png io

def read_png(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(path + ": not a PNG")
    pos, idat, plte, trns = 8, [], None, None
    w = h = depth = ctype = interlace = 0
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            plte = body
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if depth != 8 or interlace:
        raise ValueError("%s: only 8-bit non-interlaced PNG is supported (depth %d, interlace %d)"
                         % (path, depth, interlace))
    bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    raw = zlib.decompress(b"".join(idat))
    stride = w * bpp
    out = bytearray(h * stride)
    prev = bytearray(stride)
    for y in range(h):
        ft = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif ft == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 255
        out[y * stride:(y + 1) * stride] = line
        prev = line
    rgba = bytearray(w * h * 4)
    if ctype == 6:
        rgba[:] = out
    elif ctype == 2:
        rgba[0::4], rgba[1::4], rgba[2::4] = out[0::3], out[1::3], out[2::3]
        rgba[3::4] = b"\xff" * (w * h)
    elif ctype == 0:
        rgba[0::4] = rgba[1::4] = rgba[2::4] = out
        rgba[3::4] = b"\xff" * (w * h)
    elif ctype == 4:
        rgba[0::4] = rgba[1::4] = rgba[2::4] = out[0::2]
        rgba[3::4] = out[1::2]
    else:
        pal = [(plte[i], plte[i + 1], plte[i + 2]) for i in range(0, len(plte), 3)]
        alpha = list(trns or b"") + [255] * (len(pal) - len(trns or b""))
        lut = [bytes((r, g, b, alpha[i])) for i, (r, g, b) in enumerate(pal)]
        rgba[:] = b"".join(lut[i] for i in out)
    return w, h, rgba, ctype


def write_png(path, w, h, rgba):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    stride = w * 4
    raw = b"".join(b"\x00" + bytes(rgba[y * stride:(y + 1) * stride]) for y in range(h))
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


# ----------------------------------------------------------------------------- ops

def downscale(w, h, rgba, factor):
    """Box filter by an integer factor, alpha-weighted so transparent texels do not darken edges."""
    nw, nh = w // factor, h // factor
    out = bytearray(nw * nh * 4)
    n = factor * factor
    for y in range(nh):
        for x in range(nw):
            r = g = b = a = 0
            for dy in range(factor):
                o = ((y * factor + dy) * w + x * factor) * 4
                for _ in range(factor):
                    al = rgba[o + 3]
                    r += rgba[o] * al
                    g += rgba[o + 1] * al
                    b += rgba[o + 2] * al
                    a += al
                    o += 4
            i = (y * nw + x) * 4
            if a:
                out[i], out[i + 1], out[i + 2] = r // a, g // a, b // a
            out[i + 3] = a // n
    return nw, nh, out


def luma_alpha(rgba):
    for i in range(0, len(rgba), 4):
        rgba[i + 3] = max(rgba[i], rgba[i + 1], rgba[i + 2])


def cmd_info(args):
    for p in args:
        w, h, rgba, ctype = read_png(p)
        amin, amax = min(rgba[3::4]), max(rgba[3::4])
        kind = {0: "grey", 2: "RGB", 3: "palette", 4: "grey+alpha", 6: "RGBA"}[ctype]
        note = "no alpha: shape is in RGB, use --luma-alpha when packing" if amin == 255 else "alpha %d..%d" % (amin, amax)
        print("%s  %dx%d  %s  %s" % (p, w, h, kind, note))


def cmd_pack(args):
    opts = {"--cols": None, "--cell": None}
    flags = set()
    files = []
    it = iter(args)
    for a in it:
        if a in opts:
            opts[a] = int(next(it))
        elif a.startswith("--"):
            flags.add(a)
        else:
            files.append(a)
    out, inputs = files[0], files[1:]
    if not inputs:
        raise SystemExit("pack: no input files")
    cells = []
    for p in inputs:
        w, h, rgba, _ = read_png(p)
        if opts["--cell"]:
            if w % opts["--cell"] or h != w:
                raise SystemExit("%s: %dx%d cannot be reduced to %d by an integer factor" % (p, w, h, opts["--cell"]))
            if w != opts["--cell"]:
                w, h, rgba = downscale(w, h, rgba, w // opts["--cell"])
        if "--luma-alpha" in flags:
            luma_alpha(rgba)
        cells.append((w, h, rgba))
    cw, ch = cells[0][0], cells[0][1]
    if any((w, h) != (cw, ch) for w, h, _ in cells):
        raise SystemExit("pack: inputs differ in size; pass --cell to normalise them")
    cols = opts["--cols"] or int(math.ceil(math.sqrt(len(cells))))
    rows = int(math.ceil(len(cells) / cols))
    if "--pad" in flags:           # repeat frames so no cell is empty (safe for random frame picks)
        cells += [cells[i % len(inputs)] for i in range(cols * rows - len(cells))]
    sheet = bytearray(cols * cw * rows * ch * 4)
    for i, (_, _, rgba) in enumerate(cells):
        cx, cy = (i % cols) * cw, (i // cols) * ch
        for y in range(ch):
            d = ((cy + y) * cols * cw + cx) * 4
            sheet[d:d + cw * 4] = rgba[y * cw * 4:(y + 1) * cw * 4]
    write_png(out, cols * cw, rows * ch, sheet)
    print("%s  %dx%d  frames=%d  SubImageSize x=%d y=%d  cell=%dx%d" %
          (out, cols * cw, rows * ch, len(cells), cols, rows, cw, ch))


def _hash(ix, iy, seed):
    n = (ix * 374761393 + iy * 668265263 + seed * 1442695041) & 0xffffffff
    n = ((n ^ (n >> 13)) * 1274126177) & 0xffffffff
    return ((n ^ (n >> 16)) & 0xffff) / 65535.0


def _value_noise(x, y, period, seed):
    x0, y0 = int(math.floor(x)), int(math.floor(y))
    fx, fy = x - x0, y - y0
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    a = _hash(x0 % period, y0 % period, seed)
    b = _hash((x0 + 1) % period, y0 % period, seed)
    c = _hash(x0 % period, (y0 + 1) % period, seed)
    d = _hash((x0 + 1) % period, (y0 + 1) % period, seed)
    return a + (b - a) * fx + (c - a) * fy + (a - b - c + d) * fx * fy


def cmd_shape(args):
    kind, out = args[0], args[1]
    size = int(args[args.index("--size") + 1]) if "--size" in args else 256
    px = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            u, v = (x + 0.5) / size * 2 - 1, (y + 0.5) / size * 2 - 1
            r = math.hypot(u, v)
            i = (y * size + x) * 4
            if kind == "noise":
                val, amp, freq = 0.0, 0.5, 4
                for octave in range(5):
                    val += amp * _value_noise(x / size * freq, y / size * freq, freq, octave)
                    amp *= 0.5
                    freq *= 2
                c = max(0, min(255, int((val - 0.2) / 0.6 * 255)))
                px[i:i + 4] = bytes((c, c, c, 255))
                continue
            if kind == "band":              # ribbon / beam mask: bright core line across V, soft falloff
                d = abs(v)
                val = min(1.0, math.exp(-(d / 0.16) ** 2) + 0.45 * max(0.0, 1 - d) ** 2)
                c = int(val * 255)
                px[i:i + 4] = bytes((c, c, c, 255))
                continue
            if kind == "glow":
                a = max(0.0, 1 - r) ** 2.2
            elif kind == "ring":
                a = max(0.0, 1 - abs(r - 0.78) / 0.14) ** 1.5 * (1 if r < 1 else 0)
            elif kind == "spark":           # horizontal streak: long in U, thin in V, hot centre
                a = max(0.0, 1 - abs(u)) ** 1.5 * max(0.0, 1 - abs(v) / 0.12) ** 2
            else:
                raise SystemExit("shape: unknown kind %s (glow | ring | spark | noise | band)" % kind)
            px[i:i + 4] = bytes((255, 255, 255, max(0, min(255, int(a * 255)))))
    write_png(out, size, size, px)
    print("%s  %dx%d  %s" % (out, size, size, kind))


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ("info", "pack", "shape"):
        print(__doc__)
        raise SystemExit(2)
    {"info": cmd_info, "pack": cmd_pack, "shape": cmd_shape}[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
