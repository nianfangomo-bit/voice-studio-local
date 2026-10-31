# 第三方与授权边界

核对日期：2026-10-31。本说明是工程分发核对记录，不是法律意见。

GPT-SoVITS 可以用于本项目这种本地工作台集成：其代码 MIT 许可允许使用、修改、分发和销售副本，但必须保留版权与许可文本。官方基础模型仓库 lj1995/GPT-SoVITS 当前也标注 MIT。这不意味着所有第三方模型、运行库、声线或录音都自动获得同样授权。

- 代码许可：https://github.com/RVC-Boss/GPT-SoVITS/blob/20250606v2pro/LICENSE
- 基础模型声明：https://huggingface.co/lj1995/GPT-SoVITS/blob/main/README.md
- 官方用法：https://github.com/RVC-Boss/GPT-SoVITS/blob/main/docs/cn/README.md

本预发布包不分发 GPT-SoVITS 引擎、基础权重、Python 运行时、CUDA 库、FFmpeg、ASR 权重或个人声线。它们由用户从可信来源配置。完整离线版发布前，须逐项记录中国语音/文本模型、说话人模型、ASR、Python/PyTorch/CUDA 与 FFmpeg 的版本、来源、许可和再分发要求；MIT 不能覆盖这些独立许可。不要将整个个人整合包直接公开。

前端捆绑 React/ReactDOM 等第三方 JavaScript；当前构建保留了 app.js 末尾的许可注释，完整许可另附于 licenses 目录，重新构建时不得删去。旧分句/音频校验核心来自本项目已有工作流，仅打包代码，不打包配置、模型、参考录音或历史作品。

使用者必须有声音素材的合法使用权限。开源代码许可不等于获得他人的声音、姓名、肖像或内容使用权。不能冒充、欺诈或误导他人。未审核的模型权重不能自动加载。

Codex 是开发工具，不为本软件提供认证、售后或质量担保；上述交付验收标准是本项目依据官方开发建议制定的自有标准。
