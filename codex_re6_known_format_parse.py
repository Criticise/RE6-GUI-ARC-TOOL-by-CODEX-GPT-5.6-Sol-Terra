from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable


FORMAT_MAGIC_LMT = b"LMT\x00"
FORMAT_MAGIC_SPC = b"SPAC"
FORMAT_MAGIC_SRQ = b"SREQ"
LMT_CONTROL_MODEL_SUFFIXES = frozenset({".mod", ".dom", ".glb", ".gltf"})
LMT_GLTF_RUNNER_ENV_VARS = (
    "RE6_LMT_GLTF_RUNNER",
    "RE6_LMT_TO_GLTF_RUNNER",
)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ASCII_STAGE_ROOT_NAME = "codex_re6_arc_audio"
MTF_SOUND_TOOL_DOWNLOAD_URL = (
    "https://raw.githubusercontent.com/LuBuCake/MTF.SoundTool/main/"
    "MTF.SoundTool/MTF.SoundTool.Versioning/MTF.SoundTool/latest.zip"
)
FFMPEG_WINDOWS_DOWNLOAD_URL = (
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
)
DEPENDENCY_DOWNLOAD_CHUNK_SIZE = 1024 * 1024
DEPENDENCY_EXE_MAX_BYTES = 512 * 1024 * 1024
_DEPENDENCY_INSTALL_LOCK = threading.Lock()
LMT67_TRACK_TYPE_NAMES = {
    0: "LocalRotation",
    1: "LocalPosition",
    2: "LocalScale",
    3: "AbsoluteRotation",
    4: "AbsolutePosition",
}
LMT67_TRACK_TYPE_TO_CHANNEL = {
    0: "Rotation",
    1: "Position",
    2: "Scale",
    3: "Rotation",
    4: "Position",
}
LMT67_COMPRESSION_NAMES = {
    0: "None",
    1: "SingleVector3",
    2: "StepRotationQuat3",
    3: "LinearVector3",
    4: "BiLinearVector3_16bit",
    5: "BiLinearVector3_8bit",
    6: "LinearRotationQuat4_14bit",
    7: "BiLinearRotationQuat4_7bit",
    11: "BiLinearRotationQuatXW_14bit",
    12: "BiLinearRotationQuatYW_14bit",
    13: "BiLinearRotationQuatZW_14bit",
    14: "BiLinearRotationQuat4_11bit",
    15: "BiLinearRotationQuat4_9bit",
}


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def safe_slug(text: str) -> str:
    collapsed = re.sub(r"[^A-Za-z0-9._-]+", "_", str(text).strip())
    collapsed = collapsed.strip("._")
    return collapsed or "item"


def natural_sort_key(text: str) -> tuple[Any, ...]:
    parts = re.split(r"(\d+)", str(text))
    key: list[Any] = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            key.append(int(part))
        else:
            key.append(part.lower())
    return tuple(key)


def write_json(path: Path, payload: Any) -> None:
    ensure_parent(path)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary_path = Path(temporary_name)
    file_handle: Any = None
    try:
        file_handle = os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n")
        with file_handle:
            file_handle.write(json.dumps(payload, indent=2, ensure_ascii=False))
            file_handle.flush()
            os.fsync(file_handle.fileno())
        os.replace(temporary_path, path)
    except Exception:
        if file_handle is None:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
        temporary_path.unlink(missing_ok=True)
        raise


def _display_magic(data: bytes) -> str:
    parts: list[str] = []
    for value in data:
        if 32 <= value <= 126:
            parts.append(chr(value))
        elif value == 0:
            parts.append("\\0")
        else:
            parts.append(f"\\x{value:02x}")
    return "".join(parts)


def _sorted_count_map(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _rounded_float_list(values: tuple[float, ...] | list[float]) -> list[float]:
    return [round(float(value), 6) for value in values]


def _extract_ascii_strings(data: bytes, *, min_length: int = 4) -> list[str]:
    matches = re.findall(rb"[ -~]{%d,}" % min_length, data)
    ordered: list[str] = []
    seen: set[str] = set()
    for match in matches:
        text = match.decode("ascii", errors="replace")
        if text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def _extract_utf16le_strings(data: bytes, *, min_length: int = 4) -> list[str]:
    pattern = rb"(?:(?:[\x20-\x7E]\x00)|(?:[\x80-\xFF][\x00-\xFF])){%d,}" % min_length
    matches = re.findall(pattern, data)
    ordered: list[str] = []
    seen: set[str] = set()
    for match in matches:
        try:
            text = match.decode("utf-16le", errors="replace").strip("\x00")
        except Exception:
            continue
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def find_ffmpeg_exe(search_root: Path | None = None) -> Path | None:
    explicit = str(os.environ.get("RE6_FFMPEG_EXE", "")).strip()
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if search_root is not None:
        candidates.extend(
            [
                search_root / "third_party" / "FFmpeg" / "bin" / "ffmpeg.exe",
                search_root / "third_party" / "ffmpeg" / "bin" / "ffmpeg.exe",
                search_root / "FFmpeg" / "bin" / "ffmpeg.exe",
                search_root / "ffmpeg.exe",
            ]
        )
        for parent in [search_root, *search_root.parents]:
            candidates.append(parent / "ffmpeg.exe")
            for child_name in ("ffmpeg", "FFmpeg", "tools", "Tools", "修改器"):
                child_dir = parent / child_name
                if not child_dir.exists() or not child_dir.is_dir():
                    continue
                try:
                    for candidate in child_dir.rglob("ffmpeg.exe"):
                        candidates.append(candidate)
                        if len(candidates) >= 128:
                            break
                except Exception:
                    continue
    try:
        completed = subprocess.run(
            ["where", "ffmpeg"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            creationflags=NO_WINDOW,
        )
        for line in completed.stdout.splitlines():
            text = line.strip()
            if text:
                candidates.append(Path(text))
    except Exception:
        pass
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def bundled_ffmpeg_path(script_dir: Path) -> Path:
    return script_dir / "third_party" / "FFmpeg" / "bin" / "ffmpeg.exe"


def bundled_mtf_sound_tool_path(script_dir: Path) -> Path:
    return script_dir / "third_party" / "MT Framework Sound Tool" / "MTFSoundTool.exe"


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _valid_windows_executable(path: Path, *, minimum_size: int) -> bool:
    try:
        if not path.is_file() or path.stat().st_size < minimum_size:
            return False
        with path.open("rb") as handle:
            return handle.read(2) == b"MZ"
    except OSError:
        return False


def _validate_ffmpeg_executable(path: Path) -> str:
    if not _valid_windows_executable(path, minimum_size=1024 * 1024):
        raise RuntimeError(f"Downloaded FFmpeg executable is invalid: {path}")
    completed = subprocess.run(
        [str(path), "-hide_banner", "-version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=20,
        creationflags=NO_WINDOW,
    )
    output = (completed.stdout or completed.stderr).strip()
    if completed.returncode != 0 or "ffmpeg version" not in output.lower():
        raise RuntimeError(
            f"Downloaded FFmpeg failed its version check ({completed.returncode}): {output[:500]}"
        )
    return output.splitlines()[0].strip()


def _download_dependency_zip(
    url: str,
    destination: Path,
    *,
    label: str,
    progress: Callable[[str], None] | None = None,
) -> None:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "RE6-ARC-Tool-CODE-X/1.0",
            "Accept": "application/zip,application/octet-stream,*/*",
        },
    )
    if progress is not None:
        progress(f"Downloading {label}...")
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
        total_text = str(response.headers.get("Content-Length") or "").strip()
        total = int(total_text) if total_text.isdigit() else 0
        downloaded = 0
        next_report = 10
        while True:
            chunk = response.read(DEPENDENCY_DOWNLOAD_CHUNK_SIZE)
            if not chunk:
                break
            output.write(chunk)
            downloaded += len(chunk)
            if progress is not None and total > 0:
                percent = min(100, int(downloaded * 100 / total))
                if percent >= next_report:
                    progress(f"Downloading {label}: {percent}%")
                    next_report = min(100, ((percent // 10) + 1) * 10)
    if not destination.is_file() or destination.stat().st_size < 1024:
        raise RuntimeError(f"{label} download is empty or incomplete.")


def _extract_executable_from_zip(
    zip_path: Path,
    destination: Path,
    *,
    executable_name: str,
    preferred_member_suffix: str = "",
) -> None:
    with zipfile.ZipFile(zip_path, "r") as archive:
        candidates = [
            info
            for info in archive.infolist()
            if not info.is_dir()
            and Path(info.filename.replace("\\", "/")).name.lower() == executable_name.lower()
        ]
        preferred_suffix = preferred_member_suffix.replace("\\", "/").lower().lstrip("/")
        if preferred_suffix:
            preferred = [
                info
                for info in candidates
                if info.filename.replace("\\", "/").lower().endswith(preferred_suffix)
            ]
            if preferred:
                candidates = preferred
        if not candidates:
            raise RuntimeError(f"{executable_name} was not found in {zip_path.name}.")
        candidates.sort(key=lambda info: (len(info.filename), info.filename.lower()))
        member = candidates[0]
        if member.file_size <= 0 or member.file_size > DEPENDENCY_EXE_MAX_BYTES:
            raise RuntimeError(
                f"Unexpected {executable_name} size in the downloaded ZIP: {member.file_size}"
            )
        ensure_parent(destination)
        with archive.open(member, "r") as source, destination.open("wb") as output:
            shutil.copyfileobj(source, output, length=DEPENDENCY_DOWNLOAD_CHUNK_SIZE)


def _install_dependency_executable(
    *,
    url: str,
    target_path: Path,
    executable_name: str,
    label: str,
    preferred_member_suffix: str = "",
    validator: Callable[[Path], str] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    stage_root = Path(tempfile.mkdtemp(prefix="re6_arc_dependency_"))
    zip_path = stage_root / "download.zip"
    extracted_path = stage_root / executable_name
    installing_path = target_path.with_name(f".{target_path.name}.installing")
    try:
        _download_dependency_zip(url, zip_path, label=label, progress=progress)
        _extract_executable_from_zip(
            zip_path,
            extracted_path,
            executable_name=executable_name,
            preferred_member_suffix=preferred_member_suffix,
        )
        if not _valid_windows_executable(extracted_path, minimum_size=1024 * 1024):
            raise RuntimeError(f"Downloaded {label} executable failed PE validation.")
        version = validator(extracted_path) if validator is not None else ""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            installing_path.unlink()
        except FileNotFoundError:
            pass
        shutil.copy2(extracted_path, installing_path)
        os.replace(installing_path, target_path)
        if not _valid_windows_executable(target_path, minimum_size=1024 * 1024):
            raise RuntimeError(f"Installed {label} executable failed final validation.")
        if validator is not None:
            version = validator(target_path)
        if progress is not None:
            progress(f"Installed {label}: {target_path}")
        return {
            "name": label,
            "path": str(target_path),
            "download_url": url,
            "sha256": _sha256_path(target_path),
            "version": version,
            "installed": True,
        }
    finally:
        try:
            installing_path.unlink()
        except FileNotFoundError:
            pass
        shutil.rmtree(stage_root, ignore_errors=True)


def ensure_audio_dependencies(
    script_dir: Path,
    *,
    install_mtf_sound_tool: bool = True,
    install_ffmpeg: bool = True,
    progress: Callable[[str], None] | None = None,
    mtf_download_url: str = MTF_SOUND_TOOL_DOWNLOAD_URL,
    ffmpeg_download_url: str = FFMPEG_WINDOWS_DOWNLOAD_URL,
    raise_on_error: bool = False,
) -> dict[str, Any]:
    """Install missing audio executables and remove all transient downloads."""
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    with _DEPENDENCY_INSTALL_LOCK:
        if install_mtf_sound_tool:
            try:
                mtf_path = bundled_mtf_sound_tool_path(script_dir)
                if _valid_windows_executable(mtf_path, minimum_size=1024 * 1024):
                    results.append(
                        {
                            "name": "MT Framework Sound Tool",
                            "path": str(mtf_path),
                            "sha256": _sha256_path(mtf_path),
                            "installed": False,
                        }
                    )
                else:
                    results.append(
                        _install_dependency_executable(
                            url=mtf_download_url,
                            target_path=mtf_path,
                            executable_name="MTFSoundTool.exe",
                            label="MT Framework Sound Tool",
                            progress=progress,
                        )
                    )
            except Exception as exc:
                failures.append({"name": "MT Framework Sound Tool", "error": str(exc)})
        if install_ffmpeg:
            try:
                ffmpeg_path = bundled_ffmpeg_path(script_dir)
                if _valid_windows_executable(ffmpeg_path, minimum_size=1024 * 1024):
                    version = _validate_ffmpeg_executable(ffmpeg_path)
                    results.append(
                        {
                            "name": "FFmpeg",
                            "path": str(ffmpeg_path),
                            "sha256": _sha256_path(ffmpeg_path),
                            "version": version,
                            "installed": False,
                        }
                    )
                else:
                    results.append(
                        _install_dependency_executable(
                            url=ffmpeg_download_url,
                            target_path=ffmpeg_path,
                            executable_name="ffmpeg.exe",
                            label="FFmpeg",
                            preferred_member_suffix="/bin/ffmpeg.exe",
                            validator=_validate_ffmpeg_executable,
                            progress=progress,
                        )
                    )
            except Exception as exc:
                failures.append({"name": "FFmpeg", "error": str(exc)})
    payload = {
        "dependencies": results,
        "installed_count": sum(1 for item in results if bool(item.get("installed"))),
        "failure_count": len(failures),
        "failures": failures,
        "temporary_downloads_retained": 0,
    }
    if failures and raise_on_error:
        details = "; ".join(f"{item['name']}: {item['error']}" for item in failures)
        raise RuntimeError(f"Audio dependency installation failed: {details}")
    return payload


def _trim_capture_text(text: str, *, limit: int = 4000) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 32].rstrip() + "\n... <trimmed>"


def find_lmt_gltf_runner(search_root: Path | None = None) -> dict[str, Any]:
    candidates: list[tuple[str, Path]] = []
    for env_name in LMT_GLTF_RUNNER_ENV_VARS:
        explicit = str(os.environ.get(env_name, "")).strip()
        if explicit:
            candidates.append((f"env:{env_name}", Path(explicit)))
    if search_root is not None:
        local_names = (
            "codex_re6_lmt_gltf_runner.py",
            "codex_re6_lmt_gltf_runner.cmd",
            "codex_re6_lmt_gltf_runner.bat",
            "codex_re6_lmt_gltf_runner.exe",
        )
        for parent in [search_root, *search_root.parents]:
            for name in local_names:
                candidates.append((f"search:{parent}", parent / name))
    searched: list[str] = []
    seen: set[str] = set()
    for source_name, candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        searched.append(str(candidate))
        if candidate.exists() and candidate.is_file():
            return {
                "status": "configured",
                "path": str(candidate),
                "source": source_name,
                "searched": searched[:24],
            }
    return {
        "status": "missing",
        "path": "",
        "source": "",
        "searched": searched[:24],
    }


def _build_lmt_gltf_runner_command(
    runner_path: Path,
    *,
    control_gltf_path: Path,
    lmt_source_path: Path,
    output_model_path: Path,
    job_json_path: Path | None,
) -> list[str]:
    suffix = runner_path.suffix.lower()
    args = [
        "--control",
        str(control_gltf_path),
        "--lmt",
        str(lmt_source_path),
        "--out",
        str(output_model_path),
    ]
    if job_json_path is not None:
        args.extend(["--job-json", str(job_json_path)])
    if suffix == ".py":
        return [sys.executable, str(runner_path), *args]
    return [str(runner_path), *args]


def _ascii_stage_dir(label: str) -> Path:
    root = Path(tempfile.gettempdir()) / ASCII_STAGE_ROOT_NAME
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"{safe_slug(label)}_", dir=str(root)))


def _parse_wave_metadata(wav_bytes: bytes) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "riff_magic": wav_bytes[:4].decode("ascii", errors="replace") if len(wav_bytes) >= 4 else "",
        "wave_magic": wav_bytes[8:12].decode("ascii", errors="replace") if len(wav_bytes) >= 12 else "",
        "chunk_ids": [],
    }
    if len(wav_bytes) < 12 or wav_bytes[:4] != b"RIFF" or wav_bytes[8:12] != b"WAVE":
        return metadata
    chunk_ids: list[str] = []
    pos = 12
    while pos + 8 <= len(wav_bytes):
        chunk_id = wav_bytes[pos : pos + 4]
        chunk_size = struct.unpack_from("<I", wav_bytes, pos + 4)[0]
        chunk_data_start = pos + 8
        chunk_data_end = min(len(wav_bytes), chunk_data_start + chunk_size)
        chunk_text = chunk_id.decode("ascii", errors="replace")
        chunk_ids.append(chunk_text)
        if chunk_id == b"fmt " and chunk_data_end - chunk_data_start >= 16:
            metadata["format_tag"] = struct.unpack_from("<H", wav_bytes, chunk_data_start)[0]
            metadata["channels"] = struct.unpack_from("<H", wav_bytes, chunk_data_start + 2)[0]
            metadata["sample_rate"] = struct.unpack_from("<I", wav_bytes, chunk_data_start + 4)[0]
            metadata["avg_bytes_per_sec"] = struct.unpack_from("<I", wav_bytes, chunk_data_start + 8)[0]
            metadata["block_align"] = struct.unpack_from("<H", wav_bytes, chunk_data_start + 12)[0]
            metadata["bits_per_sample"] = struct.unpack_from("<H", wav_bytes, chunk_data_start + 14)[0]
        elif chunk_id == b"data":
            data_size = max(0, chunk_data_end - chunk_data_start)
            metadata["audio_data_size"] = data_size
        pos = chunk_data_start + chunk_size + (chunk_size & 1)
    metadata["chunk_ids"] = chunk_ids
    sample_rate = int(metadata.get("sample_rate") or 0)
    avg_bytes_per_sec = int(metadata.get("avg_bytes_per_sec") or 0)
    audio_data_size = int(metadata.get("audio_data_size") or 0)
    if avg_bytes_per_sec > 0:
        metadata["duration_seconds"] = round(audio_data_size / avg_bytes_per_sec, 6)
    elif sample_rate > 0 and audio_data_size > 0:
        metadata["duration_seconds"] = round(audio_data_size / sample_rate, 6)
    return metadata


def _sha1_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _wave_data_bytes(wav_bytes: bytes) -> bytes:
    if len(wav_bytes) < 12 or wav_bytes[:4] != b"RIFF" or wav_bytes[8:12] != b"WAVE":
        raise RuntimeError("Input audio is not a RIFF/WAVE file.")
    pos = 12
    while pos + 8 <= len(wav_bytes):
        chunk_id = wav_bytes[pos : pos + 4]
        chunk_size = struct.unpack_from("<I", wav_bytes, pos + 4)[0]
        data_start = pos + 8
        data_end = data_start + chunk_size
        if data_end > len(wav_bytes):
            raise RuntimeError("WAVE chunk exceeds the input file size.")
        if chunk_id == b"data":
            return wav_bytes[data_start:data_end]
        pos = data_end + (chunk_size & 1)
    raise RuntimeError("WAVE data chunk was not found.")


def _spc_table_streams(data: bytes) -> list[dict[str, Any]]:
    if len(data) < 32 or data[:4] != FORMAT_MAGIC_SPC:
        return []
    stream_count = struct.unpack_from("<I", data, 8)[0]
    descriptor_end = struct.unpack_from("<I", data, 20)[0]
    data_start = struct.unpack_from("<I", data, 28)[0]
    if stream_count <= 0:
        return []
    descriptor_bytes = descriptor_end - 32
    if descriptor_bytes <= 0 or descriptor_bytes % stream_count != 0:
        return []
    descriptor_size = descriptor_bytes // stream_count
    if descriptor_size < 80 or data_start < descriptor_end or data_start > len(data):
        return []

    streams: list[dict[str, Any]] = []
    payload_cursor = data_start
    for index in range(stream_count):
        descriptor_offset = 32 + index * descriptor_size
        descriptor = data[descriptor_offset : descriptor_offset + descriptor_size]
        if len(descriptor) != descriptor_size or descriptor[:4] != b"RIFF" or descriptor[8:12] != b"WAVE":
            return []
        data_marker = descriptor.find(b"data", 12)
        if data_marker < 12 or data_marker + 8 > len(descriptor):
            return []
        data_size = struct.unpack_from("<I", descriptor, data_marker + 4)[0]
        payload_end = payload_cursor + data_size
        if payload_end > len(data):
            return []

        # RE6 SPC keeps fixed-size WAVE descriptors together, then stores every
        # audio payload contiguously. Move the chunks after the data header in
        # front of the real payload to produce a normal standalone WAVE file.
        wav_bytes = (
            descriptor[:data_marker]
            + descriptor[data_marker + 8 :]
            + b"data"
            + struct.pack("<I", data_size)
            + data[payload_cursor:payload_end]
        )
        riff_size = len(wav_bytes) - 8
        wav_buffer = bytearray(wav_bytes)
        struct.pack_into("<I", wav_buffer, 4, riff_size)
        wav_bytes = bytes(wav_buffer)
        wave_metadata = _parse_wave_metadata(wav_bytes)
        streams.append(
            {
                "index": index,
                "descriptor_offset": descriptor_offset,
                "descriptor_size": descriptor_size,
                "data_marker": data_marker,
                "data_offset": payload_cursor,
                "data_size": data_size,
                "riff_size": riff_size,
                "wav_bytes": wav_bytes,
                "wav_sha1": _sha1_bytes(wav_bytes),
                "wave_metadata": wave_metadata,
            }
        )
        payload_cursor = payload_end
    if payload_cursor > len(data):
        return []
    return streams


def extract_spc_wav_streams(data: bytes) -> list[bytes]:
    table_streams = _spc_table_streams(data)
    if table_streams:
        return [bytes(item["wav_bytes"]) for item in table_streams]
    riff_offset = data.find(b"RIFF")
    if riff_offset < 0 or riff_offset + 12 > len(data) or data[riff_offset + 8 : riff_offset + 12] != b"WAVE":
        raise RuntimeError("Embedded RIFF/WAVE chunk was not found in SPC.")
    riff_size = struct.unpack_from("<I", data, riff_offset + 4)[0]
    riff_end = min(len(data), riff_offset + 8 + riff_size)
    return [data[riff_offset:riff_end]]


def parse_spc_bytes(
    data: bytes,
    *,
    wav_streams: list[bytes] | None = None,
    table_streams: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if len(data) < 32 or data[:4] != FORMAT_MAGIC_SPC:
        raise RuntimeError("Unsupported SPC magic.")
    if table_streams is None:
        table_streams = _spc_table_streams(data)
    if wav_streams is None:
        wav_streams = extract_spc_wav_streams(data)
    wav_bytes = wav_streams[0]
    riff_offset = int(table_streams[0]["descriptor_offset"]) if table_streams else data.find(b"RIFF")
    riff_size = len(wav_bytes) - 8
    riff_end = riff_offset + len(wav_bytes)
    header_u32 = [struct.unpack_from("<I", data, offset)[0] for offset in range(0, min(32, len(data)), 4)]
    metadata: dict[str, Any] = {
        "magic": data[:4].decode("ascii", errors="replace"),
        "file_size": len(data),
        "header_u32": header_u32,
        "riff_offset": riff_offset,
        "riff_size": riff_size,
        "riff_end": riff_end,
        "prefix_size": riff_offset,
        "trailer_size": max(0, len(data) - riff_end),
        "audio_stream_count": len(wav_streams),
        "container_layout": "spc_wave_table" if table_streams else "embedded_riff",
    }
    metadata.update(_parse_wave_metadata(wav_bytes))
    metadata["audio_streams"] = [
        {
            "index": index,
            "wav_sha1": _sha1_bytes(stream_bytes),
            **_parse_wave_metadata(stream_bytes),
        }
        for index, stream_bytes in enumerate(wav_streams)
    ]
    return metadata


def extract_spc_wav_bytes(data: bytes) -> bytes:
    return extract_spc_wav_streams(data)[0]


def _convert_wav_bytes_to_mp3_bytes(
    wav_bytes: bytes,
    *,
    ffmpeg_exe: Path,
    label: str,
) -> bytes:
    stage_dir = _ascii_stage_dir(label)
    input_path = stage_dir / "input.wav"
    output_path = stage_dir / "output.mp3"
    try:
        input_path.write_bytes(wav_bytes)
        completed = subprocess.run(
            [
                str(ffmpeg_exe),
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(input_path),
                "-codec:a",
                "libmp3lame",
                "-q:a",
                "2",
                str(output_path),
            ],
            cwd=str(stage_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            creationflags=NO_WINDOW,
        )
        if completed.returncode != 0 or not output_path.exists():
            raise RuntimeError(
                f"ffmpeg mp3 conversion failed ({completed.returncode}). stdout={completed.stdout} stderr={completed.stderr}"
            )
        return output_path.read_bytes()
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)


def _legacy_unverified_convert_spc_file(
    source_path: Path,
    *,
    wav_output_path: Path | None = None,
    mp3_output_path: Path | None = None,
    json_output_path: Path | None = None,
    txt_output_path: Path | None = None,
    ffmpeg_exe: Path | None = None,
) -> dict[str, Any]:
    data = source_path.read_bytes()
    metadata = parse_spc_bytes(data)
    wav_bytes = extract_spc_wav_bytes(data)
    generated_files: dict[str, str] = {}
    if wav_output_path is not None:
        ensure_parent(wav_output_path)
        wav_output_path.write_bytes(wav_bytes)
        generated_files["wav"] = str(wav_output_path)
    if mp3_output_path is not None:
        if ffmpeg_exe is not None and ffmpeg_exe.exists():
            try:
                mp3_bytes = _convert_wav_bytes_to_mp3_bytes(
                    wav_bytes,
                    ffmpeg_exe=ffmpeg_exe,
                    label=source_path.stem,
                )
                ensure_parent(mp3_output_path)
                mp3_output_path.write_bytes(mp3_bytes)
                generated_files["mp3"] = str(mp3_output_path)
                metadata["mp3_status"] = "ok"
            except Exception as exc:
                metadata["mp3_status"] = f"failed: {exc}"
        else:
            metadata["mp3_status"] = "ffmpeg_missing"
    metadata["generated_files"] = generated_files
    if json_output_path is not None:
        write_json(json_output_path, metadata)
        generated_files["json"] = str(json_output_path)
    if txt_output_path is not None:
        ensure_parent(txt_output_path)
        lines = [
            f"SPC source: {source_path.name}",
            f"Magic: {metadata['magic']}",
            f"FileSize: {metadata['file_size']}",
            f"RIFF offset: {metadata['riff_offset']}",
            f"RIFF size: {metadata['riff_size']}",
            f"Trailer size: {metadata['trailer_size']}",
            f"Channels: {metadata.get('channels', '')}",
            f"SampleRate: {metadata.get('sample_rate', '')}",
            f"FormatTag: {metadata.get('format_tag', '')}",
            f"AudioDataSize: {metadata.get('audio_data_size', '')}",
            f"DurationSeconds: {metadata.get('duration_seconds', '')}",
            f"MP3 status: {metadata.get('mp3_status', 'not_requested')}",
        ]
        txt_output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        generated_files["txt"] = str(txt_output_path)
    return metadata


def convert_spc_file(
    source_path: Path,
    *,
    wav_output_path: Path | None = None,
    mp3_output_path: Path | None = None,
    json_output_path: Path | None = None,
    txt_output_path: Path | None = None,
    ffmpeg_exe: Path | None = None,
) -> dict[str, Any]:
    return _legacy_unverified_convert_spc_file(
        source_path,
        wav_output_path=wav_output_path,
        mp3_output_path=mp3_output_path,
        json_output_path=json_output_path,
        txt_output_path=txt_output_path,
        ffmpeg_exe=ffmpeg_exe,
    )


def _legacy_unverified_convert_spc_bundle_file(
    source_path: Path,
    output_root: Path,
    *,
    ffmpeg_exe: Path | None = None,
) -> dict[str, Any]:
    data = source_path.read_bytes()
    table_streams = _spc_table_streams(data)
    wav_streams = (
        [bytes(item["wav_bytes"]) for item in table_streams]
        if table_streams
        else extract_spc_wav_streams(data)
    )
    metadata = parse_spc_bytes(
        data,
        wav_streams=wav_streams,
        table_streams=table_streams,
    )
    audio_dir = output_root / "audio"
    metadata_dir = output_root / "metadata"
    audio_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    generated_files: list[str] = []

    def process_stream(item: tuple[int, bytes]) -> tuple[int, dict[str, Any], list[str]]:
        index, wav_bytes = item
        stem = f"{index:03d}"
        wav_path = audio_dir / f"{stem}.wav"
        mp3_path = audio_dir / f"{stem}.mp3"
        wav_path.write_bytes(wav_bytes)
        stream_files = [str(wav_path)]
        row: dict[str, Any] = {
            "index": index,
            "wav": str(wav_path.relative_to(output_root).as_posix()),
            "wav_sha1": _sha1_bytes(wav_bytes),
            **_parse_wave_metadata(wav_bytes),
        }
        if ffmpeg_exe is not None and ffmpeg_exe.exists():
            try:
                mp3_bytes = _convert_wav_bytes_to_mp3_bytes(
                    wav_bytes,
                    ffmpeg_exe=ffmpeg_exe,
                    label=f"{source_path.stem}_{stem}",
                )
                mp3_path.write_bytes(mp3_bytes)
                row["mp3"] = str(mp3_path.relative_to(output_root).as_posix())
                row["mp3_sha1"] = _sha1_bytes(mp3_bytes)
                row["mp3_status"] = "ok"
                stream_files.append(str(mp3_path))
            except Exception as exc:
                row["mp3_status"] = f"failed: {exc}"
        else:
            row["mp3_status"] = "ffmpeg_missing"
        return index, row, stream_files

    stream_inputs = list(enumerate(wav_streams))
    if len(stream_inputs) > 1:
        max_workers = min(len(stream_inputs), max(4, int(os.cpu_count() or 1)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            stream_results = list(executor.map(process_stream, stream_inputs))
    else:
        stream_results = [process_stream(item) for item in stream_inputs]
    stream_results.sort(key=lambda item: item[0])
    stream_rows = [row for _index, row, _files in stream_results]
    for _index, _row, stream_files in stream_results:
        generated_files.extend(stream_files)

    index_payload = dict(metadata)
    index_payload["source_name"] = source_path.name
    index_payload["audio_processing_policy"] = "external_module_only; compatible_wav_writeback"
    index_payload["streams"] = stream_rows
    index_path = metadata_dir / "index.json"
    write_json(index_path, index_payload)
    generated_files.append(str(index_path))

    template_path = metadata_dir / "container_template.bin"
    template_path.write_bytes(data)
    generated_files.append(str(template_path))

    details_path = metadata_dir / "details.txt"
    detail_lines = [
        f"SPC source: {source_path.name}",
        f"Audio streams: {len(stream_rows)}",
        "Audio processing: external module only; writeback accepts compatible WAVE format without automatic processing",
        "",
    ]
    for row in stream_rows:
        detail_lines.append(
            f"{int(row['index']):03d}: WAV={row.get('wav', '')} "
            f"MP3={row.get('mp3', '')} Duration={row.get('duration_seconds', '')} "
            f"SampleRate={row.get('sample_rate', '')}"
        )
    details_path.write_text("\n".join(detail_lines).rstrip() + "\n", encoding="utf-8")
    generated_files.append(str(details_path))
    return {
        "audio_stream_count": len(stream_rows),
        "streams": stream_rows,
        "index_path": str(index_path),
        "details_path": str(details_path),
        "generated_files": generated_files,
    }


def convert_spc_bundle_file(
    source_path: Path,
    output_root: Path,
    *,
    ffmpeg_exe: Path | None = None,
) -> dict[str, Any]:
    return _legacy_unverified_convert_spc_bundle_file(
        source_path,
        output_root,
        ffmpeg_exe=ffmpeg_exe,
    )


def _convert_audio_path_to_adpcm_wav_bytes(
    input_path: Path,
    *,
    ffmpeg_exe: Path,
    label: str,
    channels: int,
    sample_rate: int,
) -> bytes:
    stage_dir = _ascii_stage_dir(label)
    staged_input = stage_dir / f"input{input_path.suffix.lower() or '.bin'}"
    output_path = stage_dir / "output.wav"
    try:
        shutil.copy2(input_path, staged_input)
        completed = subprocess.run(
            [
                str(ffmpeg_exe),
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(staged_input),
                "-ac",
                str(max(1, channels)),
                "-ar",
                str(max(1, sample_rate)),
                "-codec:a",
                "adpcm_ms",
                str(output_path),
            ],
            cwd=str(stage_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            creationflags=NO_WINDOW,
        )
        if completed.returncode != 0 or not output_path.exists():
            raise RuntimeError(
                f"ffmpeg ADPCM conversion failed ({completed.returncode}). "
                f"stdout={completed.stdout} stderr={completed.stderr}"
            )
        return output_path.read_bytes()
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)


def _replacement_wave_data(
    input_path: Path,
    *,
    original_metadata: dict[str, Any],
    ffmpeg_exe: Path | None,
    label: str,
) -> bytes:
    if input_path.suffix.lower() == ".wav":
        wav_bytes = input_path.read_bytes()
        replacement_metadata = _parse_wave_metadata(wav_bytes)
        compatible_fields = ("format_tag", "channels", "sample_rate", "block_align")
        if all(
            int(replacement_metadata.get(field) or 0) == int(original_metadata.get(field) or 0)
            for field in compatible_fields
        ):
            return _wave_data_bytes(wav_bytes)
    if ffmpeg_exe is None or not ffmpeg_exe.exists():
        raise RuntimeError(
            "Modified SPC audio must keep the original WAVE format, or ffmpeg is required to convert it back to RE6 ADPCM."
        )
    normalized_wav = _convert_audio_path_to_adpcm_wav_bytes(
        input_path,
        ffmpeg_exe=ffmpeg_exe,
        label=label,
        channels=int(original_metadata.get("channels") or 1),
        sample_rate=int(original_metadata.get("sample_rate") or 48000),
    )
    return _wave_data_bytes(normalized_wav)


def _legacy_unverified_rebuild_spc_from_bundle(
    source_path: Path | None,
    bundle_root: Path,
    *,
    ffmpeg_exe: Path | None = None,
) -> dict[str, Any]:
    template_path = bundle_root / "metadata" / "container_template.bin"
    if source_path is not None and source_path.exists():
        source_data = source_path.read_bytes()
        source_label = source_path.stem
    elif template_path.exists():
        source_data = template_path.read_bytes()
        source_label = bundle_root.name
    else:
        raise RuntimeError("SPC source and parsed container template are both missing.")
    table_streams = _spc_table_streams(source_data)
    index_path = bundle_root / "metadata" / "index.json"
    index_payload: dict[str, Any] = {}
    if index_path.exists():
        try:
            loaded = json.loads(index_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                index_payload = loaded
        except Exception:
            index_payload = {}
    index_rows = {
        int(item.get("index") or 0): item
        for item in index_payload.get("streams", [])
        if isinstance(item, dict) and int(item.get("index") or 0) >= 0
    }

    replacements: dict[int, bytes] = {}
    modified_inputs: list[str] = []
    source_streams = table_streams
    if not source_streams:
        source_streams = [
            {
                "index": 0,
                "wav_bytes": extract_spc_wav_bytes(source_data),
                "wave_metadata": _parse_wave_metadata(extract_spc_wav_bytes(source_data)),
            }
        ]
    for stream in source_streams:
        index = int(stream["index"])
        row = index_rows.get(index, {})
        wav_rel = str(row.get("wav") or f"audio/{index:03d}.wav")
        mp3_rel = str(row.get("mp3") or f"audio/{index:03d}.mp3")
        wav_path = bundle_root / Path(wav_rel)
        mp3_path = bundle_root / Path(mp3_rel)
        wav_changed = wav_path.exists() and (
            not str(row.get("wav_sha1") or "") or _sha1_bytes(wav_path.read_bytes()) != str(row.get("wav_sha1"))
        )
        mp3_changed = mp3_path.exists() and (
            not str(row.get("mp3_sha1") or "") or _sha1_bytes(mp3_path.read_bytes()) != str(row.get("mp3_sha1"))
        )
        replacement_path = wav_path if wav_changed else (mp3_path if mp3_changed else None)
        if replacement_path is None:
            continue
        replacements[index] = _replacement_wave_data(
            replacement_path,
            original_metadata=dict(stream.get("wave_metadata") or {}),
            ffmpeg_exe=ffmpeg_exe,
            label=f"{source_label}_{index:03d}_repack",
        )
        modified_inputs.append(str(replacement_path))

    if not replacements:
        return {
            "data": source_data,
            "changed_stream_count": 0,
            "modified_inputs": [],
        }

    if not table_streams:
        parsed = parse_spc_bytes(source_data)
        riff_offset = int(parsed["riff_offset"])
        riff_end = int(parsed["riff_end"])
        replacement_wav = (bundle_root / "audio" / "000.wav").read_bytes()
        return {
            "data": source_data[:riff_offset] + replacement_wav + source_data[riff_end:],
            "changed_stream_count": 1,
            "modified_inputs": modified_inputs,
        }

    data_start = struct.unpack_from("<I", source_data, 28)[0]
    patched_prefix = bytearray(source_data[:data_start])
    payloads: list[bytes] = []
    original_payload_end = data_start
    for stream in table_streams:
        index = int(stream["index"])
        original_data_size = int(stream["data_size"])
        original_payload = source_data[original_payload_end : original_payload_end + original_data_size]
        original_payload_end += original_data_size
        payload = replacements.get(index, original_payload)
        descriptor_offset = int(stream["descriptor_offset"])
        data_marker = int(stream["data_marker"])
        original_riff_size = struct.unpack_from("<I", source_data, descriptor_offset + 4)[0]
        riff_overhead = original_riff_size - original_data_size
        struct.pack_into("<I", patched_prefix, descriptor_offset + 4, riff_overhead + len(payload))
        struct.pack_into("<I", patched_prefix, descriptor_offset + data_marker + 4, len(payload))
        payloads.append(payload)
    rebuilt = bytes(patched_prefix) + b"".join(payloads) + source_data[original_payload_end:]
    return {
        "data": rebuilt,
        "changed_stream_count": len(replacements),
        "modified_inputs": modified_inputs,
    }


def rebuild_spc_from_bundle(
    source_path: Path | None,
    bundle_root: Path,
    *,
    ffmpeg_exe: Path | None = None,
) -> dict[str, Any]:
    return _legacy_unverified_rebuild_spc_from_bundle(
        source_path,
        bundle_root,
        ffmpeg_exe=ffmpeg_exe,
    )


def parse_srq_bytes(data: bytes) -> dict[str, Any]:
    if len(data) < 16 or data[:4] != FORMAT_MAGIC_SRQ:
        raise RuntimeError("Unsupported SRQ magic.")
    header_u32 = [struct.unpack_from("<I", data, offset)[0] for offset in range(0, min(64, len(data)), 4)]
    ascii_strings = _extract_ascii_strings(data)
    utf16_strings = _extract_utf16le_strings(data)
    path_guess = ""
    for candidate in [*ascii_strings, *utf16_strings]:
        if "\\" in candidate or "/" in candidate:
            path_guess = candidate
            break
    return {
        "magic": data[:4].decode("ascii", errors="replace"),
        "file_size": len(data),
        "header_u32": header_u32,
        "ascii_strings": ascii_strings,
        "utf16le_strings": utf16_strings,
        "path_guess": path_guess,
        "hex_preview": data[:128].hex(" "),
    }


def convert_srq_file(
    source_path: Path,
    *,
    json_output_path: Path | None = None,
    txt_output_path: Path | None = None,
) -> dict[str, Any]:
    data = source_path.read_bytes()
    metadata = parse_srq_bytes(data)
    if json_output_path is not None:
        write_json(json_output_path, metadata)
    if txt_output_path is not None:
        ensure_parent(txt_output_path)
        lines = [
            f"SRQ source: {source_path.name}",
            f"Magic: {metadata['magic']}",
            f"FileSize: {metadata['file_size']}",
            f"PathGuess: {metadata['path_guess']}",
            "HeaderU32:",
            ", ".join(str(value) for value in metadata["header_u32"]),
            "ASCII Strings:",
            *metadata["ascii_strings"],
            "UTF16 Strings:",
            *metadata["utf16le_strings"],
        ]
        txt_output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return metadata


def _read_lmt67_track_min_max(data: bytes, offset: int) -> dict[str, list[float]] | None:
    if offset <= 0 or offset + 32 > len(data):
        return None
    values = struct.unpack_from("<8f", data, offset)
    return {
        "min": _rounded_float_list(values[:4]),
        "max": _rounded_float_list(values[4:8]),
    }


def _parse_lmt67_track(data: bytes, track_offset: int, track_index: int) -> dict[str, Any]:
    compression_id = data[track_offset]
    track_type_id = data[track_offset + 1]
    bone_type = data[track_offset + 2]
    bone_index = data[track_offset + 3]
    weight = struct.unpack_from("<f", data, track_offset + 4)[0]
    data_size = struct.unpack_from("<I", data, track_offset + 8)[0]
    data_offset = struct.unpack_from("<I", data, track_offset + 12)[0]
    reference_data = struct.unpack_from("<4f", data, track_offset + 16)
    extremes_offset = struct.unpack_from("<I", data, track_offset + 32)[0]
    compression_name = LMT67_COMPRESSION_NAMES.get(compression_id, f"Unknown_{compression_id}")
    track_type_name = LMT67_TRACK_TYPE_NAMES.get(track_type_id, f"Unknown_{track_type_id}")
    channel_name = LMT67_TRACK_TYPE_TO_CHANNEL.get(track_type_id, "Unknown")
    data_end = data_offset + data_size if data_offset > 0 else 0
    data_in_bounds = data_offset == 0 or data_end <= len(data)
    preview = b""
    if data_offset > 0 and data_offset < len(data):
        preview = data[data_offset : min(len(data), data_offset + min(data_size, 16))]
    track: dict[str, Any] = {
        "track_index": track_index,
        "compression_id": compression_id,
        "compression_name": compression_name,
        "track_type_id": track_type_id,
        "track_type_name": track_type_name,
        "channel_name": channel_name,
        "bone_type": bone_type,
        "bone_index": bone_index,
        "weight": round(float(weight), 6),
        "data_size": data_size,
        "data_offset": data_offset,
        "data_end": data_end,
        "data_in_bounds": data_in_bounds,
        "data_preview_hex": preview.hex(" "),
        "reference_data": _rounded_float_list(reference_data),
        "extremes_offset": extremes_offset,
    }
    min_max = _read_lmt67_track_min_max(data, extremes_offset)
    if min_max is not None:
        track["extremes"] = min_max
    return track


def _loop_frame_guess(loop_frame_raw: int) -> str:
    if loop_frame_raw == 0xFFFFFFFF:
        return "unset_or_no_loop"
    if loop_frame_raw == 0:
        return "full_loop_from_start"
    return "split_loop_point"


def parse_lmt_bytes(data: bytes) -> dict[str, Any]:
    if len(data) < 8 or data[:4] != FORMAT_MAGIC_LMT:
        raise RuntimeError("Unsupported LMT magic.")
    version = struct.unpack_from("<H", data, 4)[0]
    num_block_offsets = struct.unpack_from("<H", data, 6)[0]
    block_offsets: list[int] = []
    for index in range(num_block_offsets):
        entry_offset = 8 + index * 4
        if entry_offset + 4 > len(data):
            raise RuntimeError("LMT block-offset table is truncated.")
        block_offsets.append(struct.unpack_from("<I", data, entry_offset)[0])
    metadata: dict[str, Any] = {
        "schema_id": "codex.re6.lmt.inspection",
        "schema_version": 1,
        "magic": _display_magic(data[:4]),
        "file_size": len(data),
        "version": version,
        "num_block_offsets": num_block_offsets,
        "null_block_indices": [index for index, offset in enumerate(block_offsets) if offset == 0],
        "active_block_indices": [index for index, offset in enumerate(block_offsets) if offset != 0],
        "offset_table_preview": block_offsets[:64],
        "hex_preview": data[:128].hex(" "),
        "gltf_handoff": {
            "standalone_glb_supported": False,
            "requires_control_gltf": True,
            "control_extensions": [".mod", ".dom", ".glb", ".gltf"],
            "notes": [
                "RE6 LMT stores motion tracks, not a full renderable rig or mesh.",
                "A real LMT -> glTF/GLB export needs a compatible control skeleton. CODE X can use a RE6 MOD/DOM directly or an existing GLB/GLTF.",
                "This parser exposes the motion contract so the later handoff layer can be audited against real RE6 data.",
            ],
        },
    }
    if version != 67:
        metadata["parse_status"] = "partial_unsupported_version"
        metadata["blocks"] = []
        metadata["summary"] = {
            "supported_version": 67,
            "reason": f"Current ARC helper focuses on RE6 LMT67. Saw version {version}.",
        }
        return metadata

    blocks: list[dict[str, Any]] = []
    aggregate_track_type_names: list[str] = []
    aggregate_channel_names: list[str] = []
    aggregate_compression_names: list[str] = []
    unique_bone_indices: set[int] = set()
    for block_index, block_offset in enumerate(block_offsets):
        if block_offset == 0:
            continue
        if block_offset + 60 > len(data):
            blocks.append(
                {
                    "block_index": block_index,
                    "offset": block_offset,
                    "parse_error": "block_header_out_of_bounds",
                }
            )
            continue
        frame_table_offset = struct.unpack_from("<I", data, block_offset)[0]
        num_tracks = struct.unpack_from("<I", data, block_offset + 4)[0]
        num_frames = struct.unpack_from("<I", data, block_offset + 8)[0]
        loop_frame_raw = struct.unpack_from("<I", data, block_offset + 12)[0]
        header_floats = struct.unpack_from("<8f", data, block_offset + 16)
        unk_00 = struct.unpack_from("<I", data, block_offset + 48)[0]
        buffer_1_offset = struct.unpack_from("<I", data, block_offset + 52)[0]
        buffer_2_offset = struct.unpack_from("<I", data, block_offset + 56)[0]
        tracks: list[dict[str, Any]] = []
        parse_error = ""
        for track_index in range(num_tracks):
            track_offset = frame_table_offset + track_index * 36
            if track_offset + 36 > len(data):
                parse_error = "track_table_truncated"
                break
            track = _parse_lmt67_track(data, track_offset, track_index)
            tracks.append(track)
            aggregate_track_type_names.append(str(track["track_type_name"]))
            aggregate_channel_names.append(str(track["channel_name"]))
            aggregate_compression_names.append(str(track["compression_name"]))
            unique_bone_indices.add(int(track["bone_index"]))
        block_track_type_names = [str(track["track_type_name"]) for track in tracks]
        block_channel_names = [str(track["channel_name"]) for track in tracks]
        block_compression_names = [str(track["compression_name"]) for track in tracks]
        blocks.append(
            {
                "block_index": block_index,
                "offset": block_offset,
                "frame_table_offset": frame_table_offset,
                "num_tracks": num_tracks,
                "num_frames": num_frames,
                "loop_frame_raw": loop_frame_raw,
                "loop_frame_guess": _loop_frame_guess(loop_frame_raw),
                "header_floats": _rounded_float_list(header_floats),
                "unk_00": unk_00,
                "buffer_1_offset": buffer_1_offset,
                "buffer_2_offset": buffer_2_offset,
                "parse_error": parse_error,
                "root_motion_track_count": sum(1 for track in tracks if int(track["bone_index"]) == 255),
                "unknown_root_track_count": sum(1 for track in tracks if int(track["bone_index"]) == 254),
                "track_type_counts": _sorted_count_map(block_track_type_names),
                "channel_counts": _sorted_count_map(block_channel_names),
                "compression_counts": _sorted_count_map(block_compression_names),
                "tracks": tracks,
            }
        )
    metadata["parse_status"] = "ok"
    metadata["blocks"] = blocks
    metadata["summary"] = {
        "supported_version": 67,
        "active_block_count": len(blocks),
        "total_track_count": sum(len(block.get("tracks", [])) for block in blocks),
        "track_type_counts": _sorted_count_map(aggregate_track_type_names),
        "channel_counts": _sorted_count_map(aggregate_channel_names),
        "compression_counts": _sorted_count_map(aggregate_compression_names),
        "unique_bone_index_count": len(unique_bone_indices),
        "unique_bone_indices": sorted(unique_bone_indices),
    }
    return metadata


def _format_named_counts(counts: dict[str, int], *, empty_text: str = "none") -> str:
    if not counts:
        return empty_text
    return ", ".join(f"{name}:{count}" for name, count in counts.items())


def convert_lmt_file(
    source_path: Path,
    *,
    json_output_path: Path | None = None,
    txt_output_path: Path | None = None,
) -> dict[str, Any]:
    data = source_path.read_bytes()
    metadata = parse_lmt_bytes(data)
    generated_files: dict[str, str] = {}
    metadata["generated_files"] = generated_files
    if json_output_path is not None:
        write_json(json_output_path, metadata)
        generated_files["json"] = str(json_output_path)
    if txt_output_path is not None:
        ensure_parent(txt_output_path)
        summary = dict(metadata.get("summary") or {})
        lines = [
            f"LMT source: {source_path.name}",
            f"Magic: {metadata.get('magic', '')}",
            f"Version: {metadata.get('version', '')}",
            f"FileSize: {metadata.get('file_size', '')}",
            f"ParseStatus: {metadata.get('parse_status', '')}",
            f"Declared block slots: {metadata.get('num_block_offsets', '')}",
            f"Active blocks: {summary.get('active_block_count', 0)}",
            f"Total tracks: {summary.get('total_track_count', 0)}",
            f"Track channels: {_format_named_counts(summary.get('channel_counts', {}))}",
            f"Compression mix: {_format_named_counts(summary.get('compression_counts', {}))}",
            f"Unique bone indices: {summary.get('unique_bone_index_count', 0)}",
            "GLTF handoff: control GLB/GLTF required; standalone GLB is not emitted by this helper.",
        ]
        for block in list(metadata.get("blocks") or [])[:24]:
            lines.append(
                " | ".join(
                    [
                        f"Block {block.get('block_index', '?')}",
                        f"offset={block.get('offset', 0)}",
                        f"frames={block.get('num_frames', 0)}",
                        f"tracks={block.get('num_tracks', 0)}",
                        f"loop={block.get('loop_frame_raw', 0)}",
                        f"channels={_format_named_counts(block.get('channel_counts', {}))}",
                        f"compression={_format_named_counts(block.get('compression_counts', {}))}",
                        f"root_motion={block.get('root_motion_track_count', 0)}",
                    ]
                )
            )
        txt_output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        generated_files["txt"] = str(txt_output_path)
    return metadata


def convert_lmt_gltf_handoff_file(
    source_path: Path,
    *,
    control_gltf_path: Path,
    output_model_path: Path | None = None,
    json_output_path: Path | None = None,
    txt_output_path: Path | None = None,
    search_root: Path | None = None,
) -> dict[str, Any]:
    if not control_gltf_path.exists() or not control_gltf_path.is_file():
        raise RuntimeError(f"Control MOD/DOM/GLB/GLTF is missing: {control_gltf_path}")
    if control_gltf_path.suffix.lower() not in LMT_CONTROL_MODEL_SUFFIXES:
        raise RuntimeError(
            f"Control file must be .mod, .dom, .glb, or .gltf, got: {control_gltf_path.suffix}"
        )
    data = source_path.read_bytes()
    lmt_metadata = parse_lmt_bytes(data)
    runner_info = find_lmt_gltf_runner(search_root)
    if output_model_path is None:
        output_model_path = source_path.with_suffix(source_path.suffix + ".glb")
    output_model_path = Path(output_model_path)
    generated_files: dict[str, str] = {}
    metadata: dict[str, Any] = {
        "schema_id": "codex.re6.lmt.gltf_job",
        "schema_version": 1,
        "source_lmt": str(source_path),
        "source_lmt_name": source_path.name,
        # Kept for schema-v1 runner compatibility; the value may now also be
        # an RE6 MOD/DOM that the CODE X bridge converts internally.
        "control_gltf": {
            "path": str(control_gltf_path),
            "name": control_gltf_path.name,
            "suffix": control_gltf_path.suffix.lower(),
            "file_size": control_gltf_path.stat().st_size,
        },
        "intended_output_model": str(output_model_path),
        "intended_output_suffix": output_model_path.suffix.lower(),
        "output_status": "prepared",
        "runner": dict(runner_info),
        "runner_contract": {
            "accepted_env_vars": list(LMT_GLTF_RUNNER_ENV_VARS),
            "argv_contract": [
                "--control",
                "<control.mod|control.dom|control.gltf|control.glb>",
                "--lmt",
                "<motion.lmt>",
                "--out",
                "<output.glb>",
                "--job-json",
                "<job.json>",
            ],
        },
        "revil_reference": {
            "main_file_patterns": [".glb", ".gltf"],
            "secondary_file_patterns": [".lmt", ".bin"],
            "observed_output_name": "<working_control>_out.glb",
            "notes": [
                "Current ARC helper stages one LMT per output so the GUI cache stays one-source-one-sidecar.",
                "The CODE X Revil bridge uses the official Revil Toolset and keeps its temporary batch files outside the ARC project.",
            ],
        },
        "lmt_parse_status": lmt_metadata.get("parse_status", ""),
        "lmt_summary": lmt_metadata.get("summary", {}),
        "active_block_indices": list(lmt_metadata.get("active_block_indices", [])),
        "null_block_indices": list(lmt_metadata.get("null_block_indices", [])),
        "generated_files": generated_files,
    }
    if runner_info.get("status") != "configured":
        raise RuntimeError(
            "The CODE X Revil LMT bridge is missing. No staged-only placeholder was generated."
        )
    if json_output_path is not None:
        # The external runner receives --job-json, so the prepared contract
        # must exist before its process starts. Reusing an old job would make
        # the first run see no file and later runs see stale paths/results.
        metadata["output_status"] = "prepared"
        generated_files["json"] = str(json_output_path)
        write_json(json_output_path, metadata)
    runner_error: Exception | None = None
    if runner_info.get("status") == "configured":
        runner_path = Path(str(runner_info.get("path", "")).strip())
        ensure_parent(output_model_path)
        if output_model_path.exists():
            try:
                output_model_path.unlink()
            except Exception:
                pass
        try:
            command = _build_lmt_gltf_runner_command(
                runner_path,
                control_gltf_path=control_gltf_path,
                lmt_source_path=source_path,
                output_model_path=output_model_path,
                job_json_path=json_output_path,
            )
            metadata["runner"]["command"] = command
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                creationflags=NO_WINDOW,
            )
            metadata["runner"]["returncode"] = int(completed.returncode)
            metadata["runner"]["stdout"] = _trim_capture_text(completed.stdout)
            metadata["runner"]["stderr"] = _trim_capture_text(completed.stderr)
            if completed.returncode != 0:
                raise RuntimeError(f"runner returned {completed.returncode}")
            if not output_model_path.exists():
                raise RuntimeError("runner reported success but output model was not created")
            metadata["output_status"] = "converted"
            generated_files["model"] = str(output_model_path)
        except Exception as exc:
            metadata["output_status"] = "runner_failed"
            metadata["runner"]["detail"] = str(exc)
            runner_error = exc
    if json_output_path is not None:
        write_json(json_output_path, metadata)
        generated_files["json"] = str(json_output_path)
    if txt_output_path is not None:
        ensure_parent(txt_output_path)
        summary = dict(metadata.get("lmt_summary") or {})
        lines = [
            f"LMT handoff source: {source_path.name}",
            f"Control model: {control_gltf_path}",
            f"Control suffix: {control_gltf_path.suffix.lower()}",
            f"Intended output: {output_model_path}",
            f"Output status: {metadata.get('output_status', '')}",
            f"Runner status: {metadata.get('runner', {}).get('status', '')}",
            f"Runner source: {metadata.get('runner', {}).get('source', '')}",
            f"Track count: {summary.get('total_track_count', 0)}",
            f"Active blocks: {summary.get('active_block_count', 0)}",
            f"Channels: {_format_named_counts(summary.get('channel_counts', {}))}",
            f"Compression mix: {_format_named_counts(summary.get('compression_counts', {}))}",
            "Runner contract: the CODE X Revil bridge accepts --control --lmt --out --job-json; control may be MOD, DOM, GLB, or GLTF.",
            "Current ARC mode keeps one LMT per output so cache/UI remain stable and exportable.",
        ]
        if metadata.get("runner", {}).get("detail"):
            lines.append(f"Runner detail: {metadata['runner']['detail']}")
        txt_output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        generated_files["txt"] = str(txt_output_path)
    if runner_error is not None:
        raise RuntimeError(f"LMT GLTF runner failed for {source_path.name}: {runner_error}")
    return metadata
