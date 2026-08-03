from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable


REVIL_RELEASE_PAGE = "https://github.com/PredatorCZ/RevilLib/releases/expanded_assets/nightly"
REVIL_PINNED_ARCHIVE_URL = (
    "https://github.com/PredatorCZ/RevilLib/releases/download/nightly/"
    "RevilToolset-v2.10.201-win64.7z"
)
REVIL_USER_AGENT = "RE6-ARC-Tool-CODE-X/1.0"
CONTROL_SUFFIXES = frozenset({".mod", ".dom", ".glb", ".gltf"})
MODEL_SUFFIXES = frozenset({".mod", ".dom"})
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_INSTALL_LOCK = threading.Lock()
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ASSET_ID_RE = re.compile(r"(?<![a-z0-9])([a-z]{2}\d{4})(?!\d)", re.IGNORECASE)
_GLB_BONE_ID_RE = re.compile(r":(\d+)(?:_s)?$", re.IGNORECASE)


class RevilBridgeError(RuntimeError):
    pass


def _emit(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _clean_console(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", str(text or "")).replace("\r", "").strip()


def _valid_toolset_dir(path: Path) -> bool:
    modules = path / "modules"
    return (
        (path / "revil_toolset.exe").is_file()
        and (path / "revil_toolset.config").is_file()
        and (path / "LICENSE").is_file()
        and any(modules.glob("lmt_to_gltf.*.spk"))
        and any(modules.glob("mod_to_gltf.*.spk"))
    )


def _toolset_stamp(path: Path) -> tuple[tuple[str, int, int], ...]:
    return tuple(
        (candidate.relative_to(path).as_posix(), candidate.stat().st_size, candidate.stat().st_mtime_ns)
        for candidate in sorted(path.rglob("*"), key=lambda item: item.as_posix().casefold())
        if candidate.is_file()
    )


def _ascii_cache_root(anchor: Path | None = None) -> Path:
    explicit = str(os.environ.get("RE6_ARC_CACHE", "")).strip()
    if explicit:
        root = Path(explicit)
    elif anchor is not None and anchor.drive:
        root = Path(f"{anchor.drive}\\RE6_ARC_CACHE")
    else:
        system_drive = str(os.environ.get("SystemDrive", "C:")).rstrip("\\/")
        root = Path(f"{system_drive}\\RE6_ARC_CACHE")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _download_bytes(url: str, destination: Path, *, attempts: int = 3) -> None:
    failures: list[str] = []
    for attempt in range(1, max(1, attempts) + 1):
        destination.unlink(missing_ok=True)
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": REVIL_USER_AGENT,
                "Accept": "application/octet-stream,*/*",
                "Connection": "close",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response, destination.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
            if destination.stat().st_size < 128 * 1024:
                raise RevilBridgeError("The downloaded archive is empty or incomplete.")
            return
        except Exception as exc:
            failures.append(f"attempt {attempt}: {exc}")
            destination.unlink(missing_ok=True)
            if attempt < attempts:
                time.sleep(float(attempt))
    raise RevilBridgeError(
        "Unable to download the official Revil Toolset after "
        f"{max(1, attempts)} attempts: {'; '.join(failures)}"
    )


def _resolve_revil_archive_url() -> str:
    try:
        request = urllib.request.Request(
            REVIL_RELEASE_PAGE,
            headers={"User-Agent": REVIL_USER_AGENT, "Accept": "text/html,*/*"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            html = response.read(2 * 1024 * 1024).decode("utf-8", errors="replace")
        match = re.search(
            r'href="([^"]*/RevilToolset-v[^"/]+-win64\.7z)"',
            html,
            flags=re.IGNORECASE,
        )
        if match:
            return urllib.parse.urljoin("https://github.com", match.group(1))
    except Exception:
        pass
    return REVIL_PINNED_ARCHIVE_URL


def _extract_7z_with_windows_tar(archive_path: Path, destination: Path) -> None:
    tar_exe = shutil.which("tar.exe") or shutil.which("tar")
    if not tar_exe:
        raise RevilBridgeError("Windows tar.exe is required to extract the official Revil Toolset .7z archive.")
    destination.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [tar_exe, "-xf", str(archive_path), "-C", str(destination)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        creationflags=NO_WINDOW,
    )
    if completed.returncode != 0:
        detail = _clean_console(completed.stderr or completed.stdout)
        raise RevilBridgeError(f"Unable to extract Revil Toolset ({completed.returncode}): {detail}")


def install_revil_toolset(
    script_dir: Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> Path:
    target = script_dir / "third_party" / "RevilToolset"
    with _INSTALL_LOCK:
        if _valid_toolset_dir(target):
            return target
        cache_root = _ascii_cache_root(script_dir)
        stage = cache_root / "dependency_downloads" / f"revil_{uuid.uuid4().hex}"
        archive = stage / "RevilToolset-win64.7z"
        extracted = stage / "extracted"
        installing = target.with_name(f".{target.name}.installing")
        stage.mkdir(parents=True, exist_ok=True)
        try:
            legacy_candidates = (
                cache_root / "third_party" / "RevilToolset_v2.10.201",
                cache_root / "third_party" / "RevilToolset",
            )
            source = next((item for item in legacy_candidates if _valid_toolset_dir(item)), None)
            if source is not None:
                _emit(progress, f"Migrating cached Revil Toolset into the ARC tool: {source}")
            else:
                url = _resolve_revil_archive_url()
                _emit(progress, f"Downloading Revil Toolset: {url}")
                _download_bytes(url, archive)
                _extract_7z_with_windows_tar(archive, extracted)
                source = extracted
                if not _valid_toolset_dir(source):
                    roots = [
                        candidate.parent
                        for candidate in extracted.rglob("revil_toolset.exe")
                        if _valid_toolset_dir(candidate.parent)
                    ]
                    if len(roots) != 1:
                        raise RevilBridgeError("The extracted Revil Toolset is missing its LMT/MOD modules.")
                    source = roots[0]
            if installing.exists():
                shutil.rmtree(installing, ignore_errors=True)
            installing.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, installing)
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            os.replace(installing, target)
            if not _valid_toolset_dir(target):
                raise RevilBridgeError("The installed Revil Toolset failed final validation.")
            _emit(progress, f"Installed Revil Toolset: {target}")
            return target
        finally:
            shutil.rmtree(stage, ignore_errors=True)
            if installing.exists():
                shutil.rmtree(installing, ignore_errors=True)


def find_revil_toolset_dir(
    script_dir: Path,
    *,
    install_if_missing: bool = True,
    progress: Callable[[str], None] | None = None,
) -> Path:
    candidates: list[Path] = []
    for env_name in ("RE6_REVIL_TOOLSET_DIR", "REVIL_TOOLSET_DIR", "RE6_REVIL_TOOLSET_EXE"):
        value = str(os.environ.get(env_name, "")).strip()
        if value:
            path = Path(value)
            candidates.append(path.parent if path.is_file() else path)
    candidates.extend(
        [
            script_dir / "third_party" / "RevilToolset",
        ]
    )
    for candidate in candidates:
        if _valid_toolset_dir(candidate):
            return candidate
    if not install_if_missing:
        raise RevilBridgeError("Revil Toolset is missing.")
    return install_revil_toolset(script_dir, progress=progress)


def _runtime_toolset_dir(toolset_dir: Path, cache_root: Path) -> Path:
    runtime = cache_root / "third_party" / "RevilToolset_runtime"
    with _INSTALL_LOCK:
        source_stamp = _toolset_stamp(toolset_dir)
        runtime_stamp = _toolset_stamp(runtime) if _valid_toolset_dir(runtime) else ()
        if source_stamp != runtime_stamp:
            installing = runtime.with_name(f".{runtime.name}.{uuid.uuid4().hex}.installing")
            shutil.rmtree(installing, ignore_errors=True)
            try:
                shutil.copytree(toolset_dir, installing)
                shutil.rmtree(runtime, ignore_errors=True)
                os.replace(installing, runtime)
            finally:
                shutil.rmtree(installing, ignore_errors=True)
    return runtime


def _configure_console_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(errors="backslashreplace")
        except (OSError, ValueError):
            pass


def _run_toolset(toolset_dir: Path, args: list[str]) -> str:
    completed = subprocess.run(
        [str(toolset_dir / "revil_toolset.exe"), *args],
        cwd=str(toolset_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        creationflags=NO_WINDOW,
    )
    output = _clean_console("\n".join(item for item in (completed.stdout, completed.stderr) if item))
    if completed.returncode != 0:
        raise RevilBridgeError(
            f"Revil Toolset failed ({completed.returncode}) while running {' '.join(args[:2])}.\n{output[-5000:]}"
        )
    return output


def _read_glb_json(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) < 20:
        raise RevilBridgeError(f"GLB is truncated: {path}")
    magic, version, declared_size = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2 or declared_size != len(data):
        raise RevilBridgeError(f"Invalid GLB v2 header: {path}")
    chunk_size, chunk_type = struct.unpack_from("<I4s", data, 12)
    if chunk_type != b"JSON" or 20 + chunk_size > len(data):
        raise RevilBridgeError(f"GLB JSON chunk is missing or truncated: {path}")
    try:
        payload = data[20 : 20 + chunk_size].rstrip(b" \t\r\n\0")
        return dict(json.loads(payload.decode("utf-8")))
    except Exception as exc:
        raise RevilBridgeError(f"Unable to parse GLB JSON: {path}: {exc}") from exc


def inspect_glb(path: Path, *, require_animation: bool = False) -> dict[str, Any]:
    document = _read_glb_json(path)
    animations = list(document.get("animations") or [])
    channels = sum(len(item.get("channels") or []) for item in animations if isinstance(item, dict))
    result = {
        "path": str(path),
        "file_size": path.stat().st_size,
        "node_count": len(document.get("nodes") or []),
        "skin_count": len(document.get("skins") or []),
        "animation_count": len(animations),
        "animation_channel_count": channels,
        "animation_names": [str(item.get("name") or "") for item in animations[:32] if isinstance(item, dict)],
    }
    if require_animation and (not animations or channels <= 0):
        raise RevilBridgeError(f"Revil output contains no usable animation channels: {path}")
    return result


def glb_bone_ids(path: Path) -> set[int]:
    document = _read_glb_json(path)
    ids: set[int] = set()
    for node in document.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        match = _GLB_BONE_ID_RE.search(str(node.get("name") or ""))
        if match:
            ids.add(int(match.group(1)))
    return ids


def lmt67_bone_ids(path: Path) -> set[int]:
    data = path.read_bytes()
    if len(data) < 8 or data[:4] != b"LMT\0":
        raise RevilBridgeError(f"Unsupported LMT header: {path}")
    version, count = struct.unpack_from("<HH", data, 4)
    if version != 67:
        raise RevilBridgeError(f"Automatic bone pairing currently requires RE6 LMT67, got {version}: {path}")
    ids: set[int] = set()
    for block_index in range(count):
        table_offset = 8 + block_index * 4
        if table_offset + 4 > len(data):
            raise RevilBridgeError(f"Truncated LMT offset table: {path}")
        block_offset = struct.unpack_from("<I", data, table_offset)[0]
        if block_offset == 0:
            continue
        if block_offset + 12 > len(data):
            raise RevilBridgeError(f"Truncated LMT block header: {path}")
        track_table, track_count = struct.unpack_from("<II", data, block_offset)
        for track_index in range(track_count):
            track_offset = track_table + track_index * 36
            if track_offset + 36 > len(data):
                raise RevilBridgeError(f"Truncated LMT track table: {path}")
            bone_id = data[track_offset + 3]
            if bone_id not in {254, 255}:
                ids.add(int(bone_id))
    return ids


def _copy_gltf_with_dependencies(source: Path, destination: Path) -> None:
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RevilBridgeError(f"Unable to read control glTF: {source}: {exc}") from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    source_root = source.parent.resolve()
    for section in ("buffers", "images"):
        for item in document.get(section) or []:
            if not isinstance(item, dict):
                continue
            uri = str(item.get("uri") or "").strip()
            if not uri or uri.startswith("data:") or "://" in uri:
                continue
            decoded = urllib.parse.unquote(uri).replace("/", os.sep)
            source_dependency = (source.parent / decoded).resolve()
            try:
                relative = source_dependency.relative_to(source_root)
            except ValueError as exc:
                raise RevilBridgeError(f"glTF dependency escapes its source folder: {uri}") from exc
            if not source_dependency.is_file():
                raise RevilBridgeError(f"glTF dependency is missing: {source_dependency}")
            target_dependency = destination.parent / relative
            target_dependency.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_dependency, target_dependency)


def _prepare_control_glb(
    control_path: Path,
    *,
    toolset_dir: Path,
    cache_root: Path,
    stage_root: Path,
) -> Path:
    suffix = control_path.suffix.lower()
    if suffix not in CONTROL_SUFFIXES:
        raise RevilBridgeError(f"Unsupported control model: {control_path}")
    if suffix == ".glb":
        target = stage_root / "control.glb"
        shutil.copy2(control_path, target)
        inspect_glb(target)
        return target
    if suffix == ".gltf":
        target = stage_root / "control.gltf"
        _copy_gltf_with_dependencies(control_path, target)
        return target

    digest = _sha256_file(control_path)
    cached = cache_root / "revil_controls" / digest[:24] / "control.glb"
    if cached.is_file():
        try:
            inspect_glb(cached)
            target = stage_root / "control.glb"
            shutil.copy2(cached, target)
            return target
        except Exception:
            shutil.rmtree(cached.parent, ignore_errors=True)
    model_copy = stage_root / f"control{suffix}"
    shutil.copy2(control_path, model_copy)
    _run_toolset(toolset_dir, ["mod_to_gltf", str(model_copy)])
    generated = model_copy.with_suffix(".glb")
    inspect_glb(generated)
    cached.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(generated, cached)
    return generated


def convert_lmt_to_glb(
    control_path: Path,
    lmt_path: Path,
    output_path: Path,
    *,
    script_dir: Path,
    install_if_missing: bool = True,
    keep_stage: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    control_path = control_path.resolve()
    lmt_path = lmt_path.resolve()
    output_path = output_path.resolve()
    if not control_path.is_file():
        raise RevilBridgeError(f"Control model is missing: {control_path}")
    if not lmt_path.is_file() or lmt_path.suffix.lower() not in {".lmt", ".bin"}:
        raise RevilBridgeError(f"LMT input is missing or unsupported: {lmt_path}")
    toolset_source = find_revil_toolset_dir(
        script_dir,
        install_if_missing=install_if_missing,
        progress=progress,
    )
    cache_root = _ascii_cache_root(script_dir)
    toolset_dir = _runtime_toolset_dir(toolset_source, cache_root)
    stage = cache_root / "revil_jobs" / uuid.uuid4().hex
    stage.mkdir(parents=True, exist_ok=False)
    try:
        _emit(progress, f"Preparing Revil control model: {control_path.name}")
        staged_control = _prepare_control_glb(
            control_path,
            toolset_dir=toolset_dir,
            cache_root=cache_root,
            stage_root=stage,
        )
        staged_lmt = stage / "motion.lmt"
        shutil.copy2(lmt_path, staged_lmt)
        batch_path = stage / "batch.json"
        batch_path.write_text(
            json.dumps([[staged_control.name, staged_lmt.name]], indent=2),
            encoding="utf-8",
        )
        _emit(progress, f"Injecting LMT into {staged_control.name} with Revil Toolset...")
        console = _run_toolset(toolset_dir, ["lmt_to_gltf", str(batch_path)])
        generated = stage / f"{staged_control.stem}_out.glb"
        if not generated.is_file():
            raise RevilBridgeError(f"Revil Toolset did not create its expected output: {generated}")
        inspection = inspect_glb(generated, require_animation=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(generated, output_path)
        missing_bones = sorted({int(value) for value in re.findall(r"Missing bone:\s*(\d+)", console)})
        return {
            "schema_id": "codex.re6.revil_lmt_gltf.result",
            "schema_version": 1,
            "status": "converted",
            "control_path": str(control_path),
            "control_suffix": control_path.suffix.lower(),
            "lmt_path": str(lmt_path),
            "output_path": str(output_path),
            "toolset_path": str(toolset_source),
            "runtime_toolset_path": str(toolset_dir),
            "missing_bone_ids": missing_bones,
            "inspection": {**inspection, "path": str(output_path)},
            "console_tail": console[-5000:],
            "stage_retained": bool(keep_stage),
            "stage_path": str(stage) if keep_stage else "",
        }
    finally:
        if not keep_stage:
            shutil.rmtree(stage, ignore_errors=True)


def _asset_id(path_text: str) -> str:
    normalized = str(path_text).replace("\\", "/").lower()
    stem_matches = list(_ASSET_ID_RE.finditer(Path(normalized).stem))
    if stem_matches:
        return stem_matches[0].group(1).lower()
    path_matches = list(_ASSET_ID_RE.finditer(normalized))
    return path_matches[-1].group(1).lower() if path_matches else ""


def _model_path_score(lmt_relative: str, model_relative: str) -> int:
    lmt_id = _asset_id(lmt_relative)
    model_id = _asset_id(model_relative)
    score = 0
    if lmt_id and lmt_id == model_id:
        score += 500
    model_path = Path(model_relative.replace("\\", "/"))
    if model_id and model_path.stem.lower() == model_id:
        score += 120
    if "/model/" in f"/{model_relative.replace('\\', '/').lower()}/":
        score += 40
    if model_path.stem.lower().endswith("x"):
        score -= 20
    return score


def auto_pair_lmts_to_models(
    lmt_items: Iterable[tuple[str, Path]],
    model_items: Iterable[tuple[str, Path]],
    *,
    script_dir: Path,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    lmts = [(str(relative).replace("\\", "/"), Path(path)) for relative, path in lmt_items]
    models = [
        (str(relative).replace("\\", "/"), Path(path))
        for relative, path in model_items
        if Path(path).is_file() and Path(path).suffix.lower() in MODEL_SUFFIXES
    ]
    if not models:
        raise RevilBridgeError("Automatic LMT pairing found no .mod/.dom model in the ARC project.")
    toolset_source = find_revil_toolset_dir(script_dir, progress=progress)
    cache_root = _ascii_cache_root(script_dir)
    toolset_dir = _runtime_toolset_dir(toolset_source, cache_root)
    model_bones: dict[str, set[int]] = {}
    mapping: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    unresolved: list[str] = []

    def bones_for(model_path: Path) -> set[int]:
        key = str(model_path.resolve()).casefold()
        if key in model_bones:
            return model_bones[key]
        stage = cache_root / "revil_pairing" / uuid.uuid4().hex
        stage.mkdir(parents=True, exist_ok=False)
        try:
            control = _prepare_control_glb(
                model_path.resolve(),
                toolset_dir=toolset_dir,
                cache_root=cache_root,
                stage_root=stage,
            )
            model_bones[key] = glb_bone_ids(control)
            return model_bones[key]
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    for lmt_relative, lmt_path in lmts:
        _emit(progress, f"Auto-pairing LMT: {lmt_relative}")
        lmt_bones = lmt67_bone_ids(lmt_path)
        lmt_id = _asset_id(lmt_relative)
        exact_models = [item for item in models if lmt_id and _asset_id(item[0]) == lmt_id]
        if lmt_id and not exact_models:
            unresolved.append(lmt_relative)
            rows.append(
                {
                    "lmt_relative_path": lmt_relative,
                    "lmt_asset_id": lmt_id,
                    "status": "unresolved",
                    "reason": f"no model with matching asset id {lmt_id} was found",
                    "selected_model_relative_path": "",
                    "selected_model_path": "",
                    "bone_coverage": 0.0,
                    "matched_bone_count": 0,
                    "lmt_bone_count": len(lmt_bones),
                    "candidate_count": 0,
                    "top_candidates": [],
                }
            )
            continue
        candidates = exact_models if lmt_id else models
        scored: list[tuple[float, int, str, Path, int, int]] = []
        for model_relative, model_path in candidates:
            bone_ids = bones_for(model_path)
            matched = len(lmt_bones & bone_ids)
            coverage = matched / max(1, len(lmt_bones))
            scored.append(
                (
                    coverage,
                    _model_path_score(lmt_relative, model_relative),
                    model_relative,
                    model_path,
                    matched,
                    len(lmt_bones),
                )
            )
        scored.sort(key=lambda item: (-item[0], -item[1], item[2].casefold()))
        best = scored[0]
        tied = [
            item
            for item in scored
            if abs(item[0] - best[0]) < 0.000001
            and (not lmt_id or item[1] == best[1])
        ]
        status = "matched"
        reason = ""
        if best[0] < 0.75:
            status = "unresolved"
            reason = f"best bone coverage is only {best[0]:.1%}"
        elif len(tied) > 1:
            status = "unresolved"
            reason = (
                "multiple models have the same path and bone score"
                if lmt_id
                else "multiple models have the same best bone coverage and the LMT has no asset id"
            )
        if status == "matched":
            mapping[lmt_relative] = str(best[3])
        else:
            unresolved.append(lmt_relative)
        rows.append(
            {
                "lmt_relative_path": lmt_relative,
                "lmt_asset_id": lmt_id,
                "status": status,
                "reason": reason,
                "selected_model_relative_path": best[2] if status == "matched" else "",
                "selected_model_path": str(best[3]) if status == "matched" else "",
                "bone_coverage": round(best[0], 6),
                "matched_bone_count": best[4],
                "lmt_bone_count": best[5],
                "candidate_count": len(scored),
                "top_candidates": [
                    {
                        "relative_path": item[2],
                        "path": str(item[3]),
                        "bone_coverage": round(item[0], 6),
                        "path_score": item[1],
                    }
                    for item in scored[:8]
                ],
            }
        )
    return {
        "schema_id": "codex.re6.lmt_model_pairing",
        "schema_version": 1,
        "mapping": mapping,
        "rows": rows,
        "unresolved": unresolved,
        "matched_count": len(mapping),
        "unresolved_count": len(unresolved),
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RevilLib bridge for RE6 LMT -> animated GLB.")
    parser.add_argument("--control", required=True, help="Control .mod/.dom/.glb/.gltf model.")
    parser.add_argument("--lmt", required=True, help="RE6 LMT67 input.")
    parser.add_argument("--out", required=True, help="Animated GLB output.")
    parser.add_argument("--job-json", default="", help="Prepared ARC handoff JSON; read-only to this runner.")
    parser.add_argument("--report-json", default="", help="Optional standalone bridge report.")
    parser.add_argument("--keep-stage", action="store_true")
    parser.add_argument("--no-install", action="store_true", help="Do not auto-download Revil Toolset.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _configure_console_streams()
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    script_dir = Path(__file__).resolve().parent
    try:
        if args.job_json:
            job_path = Path(args.job_json)
            if job_path.is_file():
                payload = json.loads(job_path.read_text(encoding="utf-8"))
                if payload.get("schema_id") != "codex.re6.lmt.gltf_job":
                    raise RevilBridgeError(f"Unexpected LMT handoff job schema: {job_path}")
        result = convert_lmt_to_glb(
            Path(args.control),
            Path(args.lmt),
            Path(args.out),
            script_dir=script_dir,
            install_if_missing=not args.no_install,
            keep_stage=bool(args.keep_stage),
            progress=lambda message: print(message, flush=True),
        )
        if args.report_json:
            report_path = Path(args.report_json)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(result, indent=2, ensure_ascii=True), flush=True)
        return 0
    except Exception as exc:
        print(f"Revil bridge failed: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
