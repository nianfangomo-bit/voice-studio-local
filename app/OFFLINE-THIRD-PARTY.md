# 离线包第三方组件说明

本包由声线工作室项目整理，用于 Windows 本地训练、转写和配音。应用代码许可见根目录 LICENSE；各第三方组件仍适用原许可，应用许可不替代它们。

## 引擎和模型

- GPT-SoVITS：RVC-Boss/GPT-SoVITS，适配 v2Pro 2025-06 整合包。MIT 许可原文随包保留于 `engine/LICENSE`。官方项目：https://github.com/RVC-Boss/GPT-SoVITS
- GPT-SoVITS 官方基础权重仓库 lj1995/GPT-SoVITS 标注 MIT：https://huggingface.co/lj1995/GPT-SoVITS 。本包不包含作者的微调声线或个人录音。
- 中文转写使用已有本地 Paraformer 模型，目录为 `engine/tools/asr/models/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch`。目录中的 README、模型配置及原始元数据一并保留。官方模型入口：https://modelscope.cn/models/iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch 。FunASR：https://github.com/modelscope/FunASR 。不要将当前另一仓库的许可证推断为任何历史权重的独立授权。
- 中文文本、语音特征、声纹等基础资产来自本机已有 GPT-SoVITS 官方整合包，并保留其随附文件。对应来源及再分发材料仍需作为公开发行的一部分逐项维护。

## 运行环境

Python、PyTorch、NVIDIA CUDA 运行库及其他 Python 软件包的版权许可保留在 `engine/runtime/LICENSE.txt`、各 `*.dist-info` 的 LICENSE / licenses / METADATA，以及库内原有授权文件中。React 等前端许可见根目录 `licenses` 和页面脚本保留的注释。

通过 `--audio-tools` 构建的新版候选包使用统一 FFmpeg/ffprobe 音频工具，源码提交为 `fbb9368226d693c99d9b0aee672c59ed1a36785b`。构建禁用 GPL、nonfree、外部库自动探测和网络协议，适用 LGPL 2.1 或以后版本；本软件通过独立进程调用。对应的完整 FFmpeg 源码、许可证、构建脚本、配置与日志随包保存于 `third-party-sources/audio-tools`。官方入口：https://ffmpeg.org/ 。没有使用 `--audio-tools` 的旧候选包不适用本段新构建说明，不作为本次正式发行资产。

构建选择记录于 `build-choices.json`。使用 `--trim-unused` 时会排除未用于本工作台中文 Paraformer 流程的 Faster-Whisper、CTranslate2、PyAV（含其附带媒体库）及 PySimpleGUI；不修改用户原环境。排除后的完整包仍须完成真实训练与配音复测。其余运行时许可证保留；组件清单仅记录来源与材料，不代表自动法律认证。

## 素材与用途

训练录音须由用户拥有或获得使用授权。模型和代码的许可不授予使用他人声音或内容的权利。声线工作室是独立项目，使用 Codex 辅助开发，与 GPT-SoVITS 作者和 OpenAI 无官方隶属或认证关系。
