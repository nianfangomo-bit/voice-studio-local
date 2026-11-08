"""执行小型 CUDA 检查与训练阶段产物验收，不安装或修改引擎。"""
from pathlib import Path


def explain_failure(detail):
    """将已知底层错误归类，保留原始日志供进一步诊断。"""
    lowered = detail.lower()
    if any(s in lowered for s in ('invalid value for --gpu-architecture', 'no kernel image', 'not compatible with the current pytorch')):
        return '当前运行环境与显卡架构不兼容，请使用经过验证的兼容环境；不要重新录音或原地升级依赖。'
    if 'out of memory' in lowered:
        return '显存不足，请关闭其他占用显卡的程序，或降低训练批量后重试。'
    return '处理未完成，请查看对应阶段日志；已有录音、校对和任务结果保留。'


def probe_cuda(torch):
    """验证实际矩阵和 FFT 运算；通过仅代表基础运算，不代表完整训练验收。"""
    result = {'ok': False, 'torch': str(torch.__version__), 'cuda': str(torch.version.cuda)}
    try:
        if not torch.cuda.is_available():
            raise RuntimeError('未检测到可用 CUDA，请检查 NVIDIA 驱动和配套运行环境。')
        result.update(device=torch.cuda.get_device_name(0), capability=list(torch.cuda.get_device_capability(0)),
                      arch_list=torch.cuda.get_arch_list())
        with torch.no_grad():
            x = torch.ones((32, 32), device='cuda', dtype=torch.float32)
            y = x @ x
            torch.cuda.synchronize()
            result['matmul'] = True
            # 使用与声纹处理类似的非连续窗口，覆盖 NVRTC/FFT 实际路径。
            wave = torch.ones(3200, device='cuda', dtype=torch.float32)
            spectrum = torch.fft.rfft(wave.unfold(0, 400, 160)).abs()
            torch.cuda.synchronize()
            if not torch.isfinite(y).all().item() or not torch.isfinite(spectrum).all().item():
                raise RuntimeError('CUDA 运算结果包含非有限数值。')
            result['fft'] = True
        result.update(ok=True, detail='基础矩阵与 FFT 运算通过；不代表完整训练、配音已验收。')
    except Exception as exc:
        result.update(error=str(exc)[-2000:], detail=explain_failure(str(exc)))
        if '未检测到可用 CUDA' in str(exc):
            result['detail'] = str(exc)
    return result


def require_cuda(torch):
    """在持有任务显卡锁后检查，失败则在加载大模型前停止。"""
    report = probe_cuda(torch)
    if not report['ok']:
        raise RuntimeError(report['detail'] + '\n底层错误：' + report.get('error', '未知'))
    return report


def validate_features(exp, selected, groups):
    """逐片段检查本阶段产物，拒绝缺失或空文件，不以退出码代替验收。"""
    for row in selected:
        for sub, suffix in groups:
            path = Path(exp) / sub / (Path(row['audio']).name + suffix)
            if not path.is_file() or path.stat().st_size == 0:
                raise RuntimeError('阶段产物缺失或为空：' + sub + '，片段 ' + row['id'])


def validate_rows(path, count):
    """检查文字或语义产物存在且数量与选定片段一致。"""
    path = Path(path)
    if not path.is_file() or len(path.read_text(encoding='utf-8').strip().splitlines()) != count:
        raise RuntimeError('阶段清单缺失或片段数量不一致：' + path.name)


def check_stage(returncode, log_path, validate=None):
    """结合返回码和产物检查判定阶段，出错时带上日志位置及真实异常。"""
    try:
        if returncode:
            raise RuntimeError('子进程退出码：' + str(returncode))
        if validate:
            validate()
    except Exception as exc:
        detail = Path(log_path).read_text(encoding='utf-8', errors='replace')
        # 首条异常可能比末尾片段报错更有用；完整细节仍在原日志。
        lines = detail.splitlines()
        errors = [line for line in lines if any(s in line.lower() for s in ('nvrtc:', 'runtimeerror:', 'error:', 'out of memory'))]
        evidence = '\n'.join(errors[:4]) or detail[-1500:]
        raise RuntimeError(explain_failure(detail) + '\n' + str(exc) + '\n日志：' + Path(log_path).name + '\n' + evidence) from exc
