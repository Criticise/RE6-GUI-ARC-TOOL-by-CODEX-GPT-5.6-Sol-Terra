from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET


TYPE_NAMES = {
    0x00: "invalid",
    0x01: "class",
    0x02: "classref",
    0x03: "bool",
    0x04: "u8",
    0x05: "u16",
    0x06: "u32",
    0x07: "u64",
    0x08: "s8",
    0x09: "s16",
    0x0A: "s32",
    0x0B: "s64",
    0x0C: "f32",
    0x0D: "f64",
    0x0E: "string",
    0x0F: "color",
    0x10: "point",
    0x11: "size",
    0x12: "rect",
    0x13: "matrix44",
    0x14: "vector3",
    0x15: "vector4",
    0x20: "cstring",
    0x22: "float2",
    0x23: "float3",
    0x24: "float4",
    0x3E: "vector2",
    0x80: "custom",
}


@dataclass
class MemberDef:
    name: str
    type_id: int
    flags: int
    size: int

    @property
    def type_name(self) -> str:
        return TYPE_NAMES.get(self.type_id, f"type_{self.type_id:02X}")


@dataclass
class ClassDef:
    hash_value: int
    members: list[MemberDef]


@dataclass
class ClassValue:
    type_index: int
    object_id: int
    class_def: ClassDef
    members: list["MemberValue"]


@dataclass
class MemberValue:
    definition: MemberDef
    count: int
    value: Any


class BinaryReader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def tell(self) -> int:
        return self.pos

    def seek(self, pos: int) -> None:
        self.pos = pos

    def read(self, size: int) -> bytes:
        chunk = self.data[self.pos : self.pos + size]
        if len(chunk) != size:
            raise RuntimeError("Unexpected EOF")
        self.pos += size
        return chunk

    def read_u8(self) -> int:
        return self.read(1)[0]

    def read_s8(self) -> int:
        return struct.unpack("<b", self.read(1))[0]

    def read_u16(self) -> int:
        return struct.unpack("<H", self.read(2))[0]

    def read_s16(self) -> int:
        return struct.unpack("<h", self.read(2))[0]

    def read_u32(self) -> int:
        return struct.unpack("<I", self.read(4))[0]

    def read_s32(self) -> int:
        return struct.unpack("<i", self.read(4))[0]

    def read_u64(self) -> int:
        return struct.unpack("<Q", self.read(8))[0]

    def read_s64(self) -> int:
        return struct.unpack("<q", self.read(8))[0]

    def read_f32(self) -> float:
        return struct.unpack("<f", self.read(4))[0]

    def read_f64(self) -> float:
        return struct.unpack("<d", self.read(8))[0]

    def read_cstring(self) -> str:
        start = self.pos
        end = self.data.find(b"\x00", start)
        if end < 0:
            raise RuntimeError("Unterminated string")
        self.pos = end + 1
        return self.data[start:end].decode("utf-8", errors="replace")


class XFSFile:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = path.read_bytes()
        self.reader = BinaryReader(self.data)
        self.layouts: list[ClassDef] = []
        self.ptr_size = 0
        self.base_offset = 0x18
        self.data_start = 0
        self.root: ClassValue | None = None

    def parse(self) -> "XFSFile":
        if self.data[:4] != b"XFS\x00":
            raise RuntimeError(f"Not an XFS file: {self.path}")

        version_major = struct.unpack_from("<H", self.data, 4)[0]
        if version_major not in (0x0F, 0x10):
            raise RuntimeError(f"Unsupported XFS version {version_major} in {self.path}")

        self.data_start = struct.unpack_from("<I", self.data, 0x14)[0]
        layout_count = struct.unpack_from("<I", self.data, 0x10)[0]
        first_offset = struct.unpack_from("<I", self.data, self.base_offset)[0] if layout_count else 0
        self.ptr_size = 8 if first_offset == layout_count * 8 else 4
        offset_format = "<Q" if self.ptr_size == 8 else "<I"
        raw_offsets = [
            struct.unpack_from(offset_format, self.data, self.base_offset + index * self.ptr_size)[0]
            for index in range(layout_count)
        ]
        self.layouts = self._parse_layouts(raw_offsets)
        self.reader.seek(self.base_offset + self.data_start)
        self.root = self._read_class_value()
        return self

    def _parse_layouts(self, raw_offsets: list[int]) -> list[ClassDef]:
        layouts: list[ClassDef] = []
        for index, rel_offset in enumerate(raw_offsets):
            start = self.base_offset + rel_offset
            reader = BinaryReader(self.data)
            reader.seek(start)
            hash_value = reader.read_u32()
            if self.ptr_size == 8:
                reader.read(4)
            member_count = self._read_ptr(reader)
            members: list[MemberDef] = []
            for _ in range(member_count):
                member_name_offset = self._read_ptr(reader)
                type_id = reader.read_u8()
                flags = reader.read_u8()
                size = reader.read_u16()
                if self.ptr_size == 8:
                    reader.read(4)
                reader.read(self.ptr_size * 4)

                name_reader = BinaryReader(self.data)
                name_reader.seek(self.base_offset + member_name_offset)
                name = name_reader.read_cstring()
                members.append(MemberDef(name=name, type_id=type_id, flags=flags, size=size))

            layouts.append(ClassDef(hash_value=hash_value, members=members))
        return layouts

    def _read_ptr(self, reader: BinaryReader) -> int:
        return reader.read_u64() if self.ptr_size == 8 else reader.read_u32()

    def _read_class_value(self) -> ClassValue | None:
        meta = self.reader.read_u32()
        active = meta & 0x1
        if not active:
            return None

        type_index = (meta >> 1) & 0x7FFF
        object_id = (meta >> 16) & 0xFFFF
        chunk_size_start = self.reader.tell()
        chunk_size = self._read_ptr(self.reader)

        class_def = self.layouts[type_index]
        members: list[MemberValue] = []
        for definition in class_def.members:
            count = self.reader.read_u32()
            value = self._read_member_value(definition, count)
            members.append(MemberValue(definition=definition, count=count, value=value))

        if self.reader.tell() != chunk_size_start + chunk_size:
            raise RuntimeError(
                f"Chunk size mismatch in {self.path}: "
                f"0x{self.reader.tell():X} != 0x{(chunk_size_start + chunk_size):X}"
            )

        return ClassValue(
            type_index=type_index,
            object_id=object_id,
            class_def=class_def,
            members=members,
        )

    def _read_member_value(self, definition: MemberDef, count: int) -> Any:
        if count == 0:
            return []

        if count == 1:
            return self._read_single_value(definition)

        return [self._read_single_value(definition) for _ in range(count)]

    def _read_single_value(self, definition: MemberDef) -> Any:
        type_id = definition.type_id
        if type_id == 0x01 or type_id == 0x02:
            return self._read_class_value()
        if type_id == 0x03:
            return bool(self.reader.read_u8())
        if type_id == 0x04:
            return self.reader.read_u8()
        if type_id == 0x05:
            return self.reader.read_u16()
        if type_id == 0x06:
            return self.reader.read_u32()
        if type_id == 0x07:
            return self.reader.read_u64()
        if type_id == 0x08:
            return self.reader.read_s8()
        if type_id == 0x09:
            return self.reader.read_s16()
        if type_id == 0x0A:
            return self.reader.read_s32()
        if type_id == 0x0B:
            return self.reader.read_s64()
        if type_id == 0x0C:
            return self.reader.read_f32()
        if type_id == 0x0D:
            return self.reader.read_f64()
        if type_id in (0x0E, 0x20):
            return self.reader.read_cstring()
        if type_id == 0x0F:
            return {
                "r": self.reader.read_u8(),
                "g": self.reader.read_u8(),
                "b": self.reader.read_u8(),
                "a": self.reader.read_u8(),
            }
        if type_id == 0x10 or type_id == 0x3E or type_id == 0x22:
            return {"x": self.reader.read_f32(), "y": self.reader.read_f32()}
        if type_id == 0x11:
            return {"w": self.reader.read_u32(), "h": self.reader.read_u32()}
        if type_id == 0x12:
            return {
                "x0": self.reader.read_s32(),
                "y0": self.reader.read_s32(),
                "x1": self.reader.read_s32(),
                "y1": self.reader.read_s32(),
            }
        if type_id == 0x13:
            return [self.reader.read_f32() for _ in range(16)]
        if type_id == 0x14:
            return {
                "x": self.reader.read_f32(),
                "y": self.reader.read_f32(),
                "z": self.reader.read_f32(),
                "w": self.reader.read_f32(),
            }
        if type_id == 0x23:
            return {
                "x": self.reader.read_f32(),
                "y": self.reader.read_f32(),
                "z": self.reader.read_f32(),
            }
        if type_id == 0x15 or type_id == 0x24:
            return {
                "x": self.reader.read_f32(),
                "y": self.reader.read_f32(),
                "z": self.reader.read_f32(),
                "w": self.reader.read_f32(),
            }
        if type_id == 0x80:
            num_strings = self.reader.read_u8()
            if num_strings != 2:
                raise RuntimeError(f"Unexpected custom string count {num_strings}")
            return {
                "type": self.reader.read_cstring(),
                "value": self.reader.read_cstring(),
            }
        if definition.size <= 0:
            raise RuntimeError(
                f"Unsupported property type 0x{type_id:02X} has no fixed RTTI size in {self.path}"
            )
        return {"raw_hex": self.reader.read(definition.size).hex()}


def class_type_name(class_value: ClassValue) -> str:
    return f"0x{class_value.class_def.hash_value:08X}"


def append_scalar(parent: ET.Element, member: MemberValue, value: Any) -> None:
    tag = member.definition.type_name
    node = ET.SubElement(parent, tag)
    node.set("name", member.definition.name)

    if isinstance(value, bool):
        node.set("value", "true" if value else "false")
    elif isinstance(value, dict):
        for key, item in value.items():
            node.set(key, format_scalar(item))
    elif isinstance(value, list):
        node.set("value", " ".join(format_scalar(item) for item in value))
    else:
        node.set("value", format_scalar(value))


def format_scalar(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.9g}"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def append_member(parent: ET.Element, member: MemberValue) -> None:
    definition = member.definition
    if member.count == 0:
        array_node = ET.SubElement(parent, "array")
        array_node.set("name", definition.name)
        array_node.set("type", definition.type_name)
        array_node.set("count", "0")
        return

    if member.count > 1:
        array_node = ET.SubElement(parent, "array")
        array_node.set("name", definition.name)
        array_node.set("type", definition.type_name)
        array_node.set("count", str(member.count))
        if definition.type_id in (0x01, 0x02):
            for item in member.value:
                item_node = ET.SubElement(array_node, definition.type_name)
                if item is not None:
                    item_node.set("type", class_type_name(item))
                    item_node.set("objectId", str(item.object_id))
                    append_class_body(item_node, item)
        else:
            for item in member.value:
                append_scalar(array_node, MemberValue(definition=definition, count=1, value=item), item)
        return

    if definition.type_id in (0x01, 0x02):
        class_node = ET.SubElement(parent, definition.type_name)
        class_node.set("name", definition.name)
        if member.value is not None:
            class_node.set("type", class_type_name(member.value))
            class_node.set("objectId", str(member.value.object_id))
            append_class_body(class_node, member.value)
        return

    append_scalar(parent, member, member.value)


def append_class_body(parent: ET.Element, class_value: ClassValue) -> None:
    for member in class_value.members:
        append_member(parent, member)


def build_xml_tree(xfs_file: XFSFile, include_rtti: bool) -> ET.ElementTree:
    if xfs_file.root is None:
        raise RuntimeError("XFS root is empty")

    root = ET.Element("xfs")
    root.set("file", str(xfs_file.path))
    root.set("ptrSize", str(xfs_file.ptr_size))
    root.set("layoutCount", str(len(xfs_file.layouts)))

    if include_rtti:
        rtti_node = ET.SubElement(root, "rtti")
        for index, layout in enumerate(xfs_file.layouts):
            class_node = ET.SubElement(rtti_node, "class")
            class_node.set("index", str(index))
            class_node.set("hash", f"0x{layout.hash_value:08X}")
            for member in layout.members:
                member_node = ET.SubElement(class_node, "member")
                member_node.set("name", member.name)
                member_node.set("type", member.type_name)
                member_node.set("flags", str(member.flags))
                member_node.set("size", str(member.size))

    class_node = ET.SubElement(root, "class")
    class_node.set("type", class_type_name(xfs_file.root))
    class_node.set("objectId", str(xfs_file.root.object_id))
    append_class_body(class_node, xfs_file.root)
    return ET.ElementTree(root)


def indent_xml(element: ET.Element, level: int = 0) -> None:
    indent = "\n" + level * "\t"
    if len(element):
        if not element.text or not element.text.strip():
            element.text = indent + "\t"
        for child in element:
            indent_xml(child, level + 1)
        if not element[-1].tail or not element[-1].tail.strip():
            element[-1].tail = indent
    if level and (not element.tail or not element.tail.strip()):
        element.tail = indent


def convert_file(input_path: Path, output_path: Path, include_rtti: bool) -> None:
    xfs_file = XFSFile(input_path).parse()
    tree = build_xml_tree(xfs_file, include_rtti=include_rtti)
    indent_xml(tree.getroot())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
