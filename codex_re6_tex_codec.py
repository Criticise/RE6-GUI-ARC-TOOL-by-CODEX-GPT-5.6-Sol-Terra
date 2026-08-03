from __future__ import annotations

import struct
import zlib
from pathlib import Path


TEX_FORMAT_MAPPER = {
    2: b"DXT1",
    14: b"DXT1",
    19: b"DXT1",
    20: b"DXT1",
    23: b"DXT5",
    24: b"DXT5",
    25: b"DXT1",
    31: b"DXT5",
    32: b"DXT5",
    35: b"DXT5",
    39: b"",
    40: b"",
    43: b"DXT1",
}


def calculate_texture_payload_size(
    width: int,
    height: int,
    depth: int,
    mipmaps: int,
    images: int,
    compression: bytes,
) -> int:
    total = 0
    for _image_index in range(max(1, int(images))):
        mip_width = max(1, int(width))
        mip_height = max(1, int(height))
        mip_depth = max(1, int(depth))
        for _mip_index in range(max(1, int(mipmaps))):
            if compression == b"DXT1":
                level_size = ((mip_width + 3) // 4) * ((mip_height + 3) // 4) * 8
            elif compression in {b"DXT3", b"DXT5"}:
                level_size = ((mip_width + 3) // 4) * ((mip_height + 3) // 4) * 16
            elif not compression:
                level_size = mip_width * mip_height * 4
            else:
                raise ValueError(f"Unsupported DDS compression: {compression!r}")
            total += level_size * mip_depth
            mip_width = max(1, mip_width // 2)
            mip_height = max(1, mip_height // 2)
            mip_depth = max(1, mip_depth // 2)
    return total


def declared_texture_compression(parsed: dict) -> bytes:
    compression_value = parsed["compression_format"]
    if isinstance(compression_value, bytes):
        if compression_value in (b"", b"\x15"):
            return b""
        if compression_value in (b"DXT1", b"DXT3", b"DXT5"):
            return compression_value
        return compression_value[:4].rstrip(b"\x00")
    return TEX_FORMAT_MAPPER.get(int(compression_value), b"")


def resolve_texture_compression(parsed: dict) -> bytes:
    declared = declared_texture_compression(parsed)
    dimensions = (
        int(parsed["width"]),
        int(parsed["height"]),
        int(parsed.get("depth", 1) or 1),
        int(parsed["num_mipmaps_per_image"]),
        int(parsed["num_images"]),
    )
    payload_size = len(bytes(parsed.get("dds_data", b"")))
    if declared in {b"", b"DXT1", b"DXT3", b"DXT5"}:
        if calculate_texture_payload_size(*dimensions, declared) == payload_size:
            return declared

    # Some edited RE6 TEX files retain the old format number after their
    # payload was replaced. Infer only when one payload layout fits exactly.
    matches = [
        candidate
        for candidate in (b"DXT1", b"DXT5", b"DXT3", b"")
        if calculate_texture_payload_size(*dimensions, candidate) == payload_size
    ]
    if matches:
        return matches[0]
    raise ValueError(
        "TEX payload does not match its declared format or any writable DDS layout: "
        f"declared={declared!r}, payload={payload_size}, dimensions={dimensions}"
    )


def calculate_linear_size(width: int, height: int, fmt: bytes) -> int:
    block_size = 8 if fmt in (b"DXT1", b"BC1", b"BC4") else 16
    return ((width + 3) >> 2) * ((height + 3) >> 2) * block_size


def build_dds_header(
    width: int,
    height: int,
    mipmaps: int,
    compression: bytes,
    cubemap: bool,
    depth: int = 1,
) -> bytes:
    flags = 0x1 | 0x2 | 0x4 | 0x1000
    caps = 0x1000
    caps2 = 0
    pitch_or_linear = 0
    if mipmaps > 0:
        flags |= 0x20000
        caps |= 0x8 | 0x400000
    if compression:
        flags |= 0x80000
        pitch_or_linear = calculate_linear_size(width, height, compression)
        pixel_flags = 0x4
        rgb_bits = rmask = gmask = bmask = amask = 0
        fourcc = compression.ljust(4, b"\x00")[:4]
    else:
        pixel_flags = 0x40 | 0x1
        rgb_bits = 32
        rmask = 0x00FF0000
        gmask = 0x0000FF00
        bmask = 0x000000FF
        amask = 0xFF000000
        fourcc = b"\x00\x00\x00\x00"
    if cubemap:
        caps2 = 0x200 | 0xFC00
    if depth > 1:
        flags |= 0x800000
        caps |= 0x8
        caps2 |= 0x200000
    header = struct.pack(
        "<4s18I",
        b"DDS ",
        124,
        flags,
        height,
        width,
        pitch_or_linear,
        max(1, depth) if depth > 1 else 0,
        mipmaps,
        *([0] * 11),
    )
    pixel_format = struct.pack(
        "<II4sIIIII",
        32,
        pixel_flags,
        fourcc,
        rgb_bits,
        rmask,
        gmask,
        bmask,
        amask,
    )
    return header + pixel_format + struct.pack("<5I", caps, caps2, 0, 0, 0)


def parse_tex_157(data: bytes, expect_magic: bytes) -> dict:
    if len(data) < 16 or data[:4] != expect_magic:
        raise ValueError("Invalid TEX157 header.")
    bits = int.from_bytes(data[4:16], "little")
    cursor = 0

    def read_bits(count: int) -> int:
        nonlocal cursor
        value = (bits >> cursor) & ((1 << count) - 1)
        cursor += count
        return value

    version = read_bits(8)
    unk = read_bits(8)
    attr = read_bits(8)
    prebias = read_bits(4)
    texture_type = read_bits(4)
    mipmaps = read_bits(6)
    width = read_bits(13)
    height = read_bits(13)
    images = read_bits(8)
    compression_format = read_bits(8)
    depth = read_bits(13)
    auto_resize = bool(read_bits(1))
    render_target = bool(read_bits(1))
    use_vtf = bool(read_bits(1))
    cube_size = 108 if images == 6 else 0
    offset_count = mipmaps * images
    header_size = 16 + cube_size + offset_count * 4
    if len(data) < header_size:
        raise ValueError("TEX157 mip offsets exceed the file.")
    offsets = list(struct.unpack_from(f"<{offset_count}I", data, 16 + cube_size)) if offset_count else []
    return {
        "header_kind": "157",
        "version": version,
        "unk": unk,
        "attr": attr,
        "prebias": prebias,
        "texture_type": texture_type,
        "num_mipmaps_per_image": mipmaps,
        "width": width,
        "height": height,
        "num_images": images,
        "compression_format": compression_format,
        "depth": depth,
        "auto_resize": auto_resize,
        "render_target": render_target,
        "use_vtf": use_vtf,
        "mipmap_offsets": offsets,
        "dds_data": data[header_size:],
    }


def parse_tex_112(data: bytes, expect_magic: bytes) -> dict:
    if len(data) < 40 or data[:4] != expect_magic:
        raise ValueError("Invalid TEX112 header.")
    version = struct.unpack_from("<H", data, 4)[0]
    packed = data[6]
    packed2 = data[7]
    mipmaps = data[8]
    images = data[9]
    width = struct.unpack_from("<H", data, 12)[0]
    height = struct.unpack_from("<H", data, 14)[0]
    compression_raw = data[20:24]
    cube_size = 108 if images == 6 else 0
    offset_count = mipmaps * images
    header_size = 40 + cube_size + offset_count * 4
    if len(data) < header_size:
        raise ValueError("TEX112 mip offsets exceed the file.")
    offsets = list(struct.unpack_from(f"<{offset_count}I", data, 40 + cube_size)) if offset_count else []
    return {
        "header_kind": "112",
        "version": version,
        "texture_type": packed & 0x0F,
        "encoded_type": (packed >> 4) & 0x0F,
        "depend_screen": bool(packed2 & 0x01),
        "render_target": bool((packed2 >> 1) & 0x01),
        "attr": (packed2 >> 2) & 0x3F,
        "num_mipmaps_per_image": mipmaps,
        "width": width,
        "height": height,
        "num_images": images,
        "compression_format_raw": compression_raw,
        "compression_format": compression_raw.rstrip(b"\x00"),
        "padding": struct.unpack_from("<H", data, 10)[0],
        "depth": struct.unpack_from("<I", data, 16)[0],
        "rgba": list(struct.unpack_from("<4f", data, 24)),
        "mipmap_offsets": offsets,
        "dds_data": data[header_size:],
    }


def parse_texture_file(data: bytes) -> tuple[str, dict]:
    if len(data) < 8:
        raise ValueError("Texture file is too small.")
    magic = data[:4]
    if magic not in (b"TEX\x00", b"RTX\x00"):
        raise ValueError(f"Unsupported texture magic: {magic!r}")
    version16 = struct.unpack_from("<H", data, 4)[0]
    parsed = parse_tex_112(data, magic) if version16 == 112 else parse_tex_157(data, magic)
    return ("tex" if magic == b"TEX\x00" else "rtex"), parsed


def convert_to_dds(parsed: dict) -> bytes:
    compression = resolve_texture_compression(parsed)
    return build_dds_header(
        int(parsed["width"]),
        int(parsed["height"]),
        int(parsed["num_mipmaps_per_image"]),
        compression,
        int(parsed["num_images"]) > 1,
        int(parsed.get("depth", 1) or 1),
    ) + bytes(parsed["dds_data"])


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", zlib.crc32(chunk_type + payload) & 0xFFFFFFFF)


def write_placeholder_png(output_path: Path, width: int, height: int) -> None:
    width = max(1, int(width))
    height = max(1, int(height))
    step = max(8, min(width, height) // 8)
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            shade = 140 if ((x // step) + (y // step)) % 2 == 0 else 96
            rows.extend((shade, shade, shade, 255))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + _png_chunk(b"IEND", b"")
    )
