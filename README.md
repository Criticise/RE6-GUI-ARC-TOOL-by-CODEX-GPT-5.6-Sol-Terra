# RE6 ARC Tool - CODE X

> A GUI-first ARC workflow specialized for **Resident Evil 6 (PC)**.
>
> 一个为 **生化危机 6 PC 版** 专门优化的 ARC 图形化工作流工具。

## English

RE6 ARC Tool - CODE X is a Windows GUI tool for inspecting, extracting,
editing, converting, and repacking Resident Evil 6 PC ARC content. It is
designed around RE6's actual ARC ordering and container behavior rather than
as a generic archive tool.

### Highlights

- GUI-first ARC workflow with drag and drop, file browsing, directory
  conversion, and per-entry actions.
- RE6-aware extract and repack paths that preserve ARC order and use TXT
  records as the structural/writeback contract.
- Native TEX <-> DDS and XFS <-> XML conversion with RE6 writeback metadata.
- Native SPC workflow: extract an SPC into a numbered audio folder plus
  metadata, edit or replace audio, rebuild the SPC, then repack it into ARC.
- Conservative TXT repack mode and an explicitly marked experimental
  all-files pack mode.
- LMT -> GLTF/GLB preview workflow through the bundled Revil Toolset bridge.
- No RE6 game assets are included in this repository or release asset.

### Quick Start

1. Download the Release `.7z` and extract it.
2. On Windows, run `启动 RE6 ARC 工具.bat`.
3. Load or drag in a RE6 PC ARC file.
4. Keep the generated TXT beside its corresponding parsed folder when
   repacking. TXT contains the order and writeback data needed by parsed
   formats.

Use the source-only export path for formats that cannot be parsed. For SPC,
edit audio files only; do not edit `metadata` unless the format is understood.
Always validate a finished mod in the game.

### Third-Party Components and Credits

This project calls or bundles external tools only for their respective
functions. Their authors retain all rights. Original license and notice files
are kept under `third_party` where supplied.

| Component | Purpose in this project | Source / project page | License / notice |
| --- | --- | --- | --- |
| Revil Toolset / ReviLib by PredatorCZ | ARC-related utilities and LMT/GLTF bridge support | https://github.com/PredatorCZ/RevilLib/releases and https://github.com/PredatorCZ/Spike/wiki/Spike | Bundled `third_party/RevilToolset/LICENSE` |
| MT Framework Sound Tool by LuBuCake / Wesky | Reference support for MT Framework sound formats such as SRQ | https://github.com/LuBuCake/MTF.SoundTool and https://residentevilmodding.boards.net/thread/15557/mt-framework-sound-tool | GPL-3.0 notice in `third_party/MT Framework Sound Tool` |
| FFmpeg | Audio-processing infrastructure | https://ffmpeg.org/ ; Windows build source: https://www.gyan.dev/ffmpeg/builds/ | GPL-3.0 notice in `third_party/FFmpeg` |

`Resident Evil`, `RE6`, and related game assets and trademarks belong to
Capcom. This is an unofficial fan-made modding tool and is not affiliated with
or endorsed by Capcom.

The CODE X Python source has no project-wide license declaration in this
release. Third-party components retain their own licenses.

## 中文

RE6 ARC Tool - CODE X 是一个面向 Windows 的图形化工具，用于检查、解包、修改、格式转换和重打包《生化危机 6》PC 版 ARC 内容。它按 RE6 实际 ARC 索引顺序和容器行为设计，不是泛用压缩包工具。

### 主要特色

- 以 GUI 为核心，支持拖拽、文件浏览、目录格式转换和单条目操作。
- RE6 专项解包与回包链路，保留 ARC 索引顺序，并用 TXT 记录结构与回写合同。
- 原生支持 TEX <-> DDS、XFS <-> XML，并保留 RE6 回写所需元数据。
- 原生支持 SPC：SPC 解包为按序号排列的音频文件夹和 metadata，修改或替换音频后可重建 SPC，再写回 ARC。
- 提供保守 TXT 回包模式，以及明确标注为实验性的全部文件打包模式。
- 通过内置 Revil Toolset 桥接提供 LMT -> GLTF/GLB 动作预览工作流。
- 本仓库和 Release 不包含任何 RE6 游戏资源。

### 快速开始

1. 下载 Release 中的 `.7z` 并解压。
2. 在 Windows 上运行 `启动 RE6 ARC 工具.bat`。
3. 载入或拖入 RE6 PC ARC 文件。
4. 回包时请保留每个解析目录对应的 TXT。TXT 保存了解析格式回写所需的顺序和结构信息。

无法解析的格式请使用源文件导出路径。SPC 只修改音频文件，不要修改 `metadata`，除非已经理解对应字段。最终 MOD 请始终在游戏内验证。

### 第三方工具与致谢

本项目仅为各自功能调用或附带下列外部工具，其作者保留全部权利。原始许可证和声明会尽量保留在 `third_party` 目录中。

| 工具 | 在本项目中的用途 | 原始项目链接 | 许可证 / 声明 |
| --- | --- | --- | --- |
| PredatorCZ 的 Revil Toolset / ReviLib | ARC 相关工具与 LMT/GLTF 桥接支持 | https://github.com/PredatorCZ/RevilLib/releases 和 https://github.com/PredatorCZ/Spike/wiki/Spike | `third_party/RevilToolset/LICENSE` |
| LuBuCake / Wesky 的 MT Framework Sound Tool | SRQ 等 MT Framework 声音格式的参考支持 | https://github.com/LuBuCake/MTF.SoundTool 和 https://residentevilmodding.boards.net/thread/15557/mt-framework-sound-tool | `third_party/MT Framework Sound Tool` 内 GPL-3.0 声明 |
| FFmpeg | 音频处理基础设施 | https://ffmpeg.org/；Windows 构建来源：https://www.gyan.dev/ffmpeg/builds/ | `third_party/FFmpeg` 内 GPL-3.0 声明 |

`Resident Evil`、`RE6`、相关游戏资源和商标均归 Capcom 所有。本工具为非官方玩家 MOD 工具，与 Capcom 不存在从属、合作或授权关系。

本次发布未为 CODE X Python 源代码声明统一许可证；第三方组件继续适用其各自许可证。
