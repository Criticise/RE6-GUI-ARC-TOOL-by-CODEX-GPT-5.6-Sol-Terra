FFmpeg - README / 请先读我
================================

中文
----
用途：为 RE6 ARC 工具及后续独立音频修改套件提供格式探测、解码和编码基础设施。
固定位置：third_party\FFmpeg\bin\ffmpeg.exe
当前验证版本：8.1.2 Essentials Build
上游项目：https://ffmpeg.org/
Windows 构建：https://www.gyan.dev/ffmpeg/builds/
自动下载：https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip
许可证：GPL-3.0（具体构建参数和第三方库见 UPSTREAM_BUILD_README.txt）

发布轻量版时可以删除 bin\ffmpeg.exe。RE6 ARC 工具检测到文件缺失后会从上述
固定发布地址自动下载 ZIP，只提取 ffmpeg.exe 到固定相对目录，并删除临时下载和
解压缓存。

FFmpeg 只提供音频格式基础能力。ARC/SPC 纯重打包流程不会自动修改音量、音高、
速度或时长，也不会为了槽位长度自动补齐或裁剪音频。

English
-------
Purpose: format probing, decoding, and encoding infrastructure for the RE6 ARC tool and its future standalone audio editing suite.
Fixed path: third_party\FFmpeg\bin\ffmpeg.exe
Verified version: 8.1.2 Essentials Build
Upstream project: https://ffmpeg.org/
Windows build: https://www.gyan.dev/ffmpeg/builds/
Automatic download: https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip
License: GPL-3.0; see UPSTREAM_BUILD_README.txt for build configuration and third-party libraries.

For a lightweight release, bin\ffmpeg.exe may be omitted. When it is missing, the RE6 ARC tool downloads the ZIP from the fixed URL above, installs only ffmpeg.exe at the fixed relative path, and removes all temporary download and extraction files.

FFmpeg provides audio-format infrastructure only. Pure ARC/SPC repacking does not automatically alter gain, pitch, speed, or duration, and does not pad or trim audio to fit a slot.
