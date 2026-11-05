# 离线包第三方组件说明

本包由声线工作室项目整理，用于 Windows 本地训练、转写和配音。应用代码许可见根目录 LICENSE；各第三方组件仍适用原许可，应用许可不替代它们。

## 引擎和模型

- GPT-SoVITS：RVC-Boss/GPT-SoVITS，适配 v2Pro 2025-06 整合包。MIT 许可原文随包保留于 `engine/LICENSE`。官方项目：https://github.com/RVC-Boss/GPT-SoVITS
- GPT-SoVITS 官方基础权重仓库 lj1995/GPT-SoVITS 标注 MIT：https://huggingface.co/lj1995/GPT-SoVITS 。本包不包含作者的微调声线或个人录音。
- 中文转写使用已有本地 Paraformer 模型，目录为 `engine/tools/asr/models/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch`。目录中的 README、模型配置及原始元数据一并保留。官方模型入口：https://modelscope.cn/models/iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch 。FunASR：https://github.com/modelscope/FunASR 。不要将当前另一仓库的许可证推断为任何历史权重的独立授权。
- 中文文本、语音特征、声纹等基础资产来自本机已有 GPT-SoVITS 官方整合包，并保留其随附文件。对应来源及再分发材料仍需作为公开发行的一部分逐项维护。

## 运行环境

2026-11-03 的干净重建使用官方 Python 3.9.13，在独立目录重新安装锁定依赖，没有复制旧 site-packages。完整安装版本表、测试范围和补充许可证收录在 `build-records/clean-build-records` 与 `build-records/runtime-notices`。后者也保留部分同版本源码归档及材料来源摘要。部分补充通知取自记录的后续上游提交，索引明确标注，不能误称为逐文件法律审计。

`torch-complex 0.4.3` 的上游元数据 `License` 字段为 UNKNOWN，但分类字段明确声明 Apache Software License。补充材料保留该精确元数据与标准 Apache 2.0 全文，不把通用许可文本冒充其原仓库的 LICENSE 文件。FunASR 已安装源码的 MIT 版权头保留，补充上游通知的提交也单独记录。

`lid.176.bin` 是 fastText 的语言识别模型，来自 Facebook/fastText 项目，按 CC BY-SA 3.0 分发，不是应用 MIT 许可的一部分。本包保留原模型，不修改权重；来源 https://fasttext.cc/docs/en/language-identification.html ，许可 https://creativecommons.org/licenses/by-sa/3.0/ 。模型作者引用：Armand Joulin、Edouard Grave、Piotr Bojanowski、Tomas Mikolov，Bag of Tricks for Efficient Text Classification（2016）。再分发或修改该资产时须遵守其署名和相同方式共享条款。

Python、PyTorch、NVIDIA CUDA 运行库及其他 Python 软件包的版权许可保留在 `engine/runtime/LICENSE.txt`、各 `*.dist-info` 的 LICENSE / licenses / METADATA，以及库内原有授权文件中。React 等前端许可见根目录 `licenses` 和页面脚本保留的注释。

通过 `--audio-tools` 构建的新版候选包使用统一 FFmpeg/ffprobe 音频工具，源码提交为 `fbb9368226d693c99d9b0aee672c59ed1a36785b`。构建禁用 GPL、nonfree、外部库自动探测和网络协议，适用 LGPL 2.1 或以后版本；本软件通过独立进程调用。对应的完整 FFmpeg 源码、许可证、构建脚本、配置与日志随包保存于 `third-party-sources/audio-tools`。官方入口：https://ffmpeg.org/ 。没有使用 `--audio-tools` 的旧候选包不适用本段新构建说明，不作为本次正式发行资产。

构建选择记录于 `build-choices.json`。使用 `--trim-unused` 时会排除未用于本工作台中文 Paraformer 流程的 Faster-Whisper、CTranslate2、PyAV（含其附带媒体库）及 PySimpleGUI；不修改用户原环境。排除后的完整包仍须完成真实训练与配音复测。其余运行时许可证保留；组件清单仅记录来源与材料，不代表自动法律认证。

干净重建环境不安装旧 `LangSegment` 和韩语分析组件 `eunjeon`。当前中文推理入口使用引擎内的 `text.LangSegmenter`，不是旧 `LangSegment` 包；本工作台不提供韩语配音。不得把此精简包宣传为完整支持官方所有多语言功能的整合环境。新环境的真实转写、各一轮训练、配音、声线复用与单句重做已通过；最终安装包必须另行验收。FunASR 声明依赖的 Whisper 等包仍按依赖关系安装，不因为本产品不直接使用就随意删除。

## 1.0.2-rc.1 字体分发调整

本候选包排除 Matplotlib 的 46 个旧 PS AFM 度量文件，原因是逐项授权证据尚未确认；不据此认定原文件侵权。保留 PDF 核心度量、常用 TTF 及许可，补充 KaTeX 字体 OFL 通知。排除清单见 build-choices.json；本软件不提供旧 PS AFM 绘图功能。原环境的训练记录不是本候选包的训练验收记录。

## 素材与用途

训练录音须由用户拥有或获得使用授权。模型和代码的许可不授予使用他人声音或内容的权利。声线工作室是独立项目，使用 Codex 辅助开发，与 GPT-SoVITS 作者和 OpenAI 无官方隶属或认证关系。
