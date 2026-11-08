"""调用随包的只读检测助手；与首次安装入口共用一套规则。"""
import json
import subprocess
from pathlib import Path


def inspect_hardware():
    """仅执行固定助手，不接受用户传入命令或可执行文件路径。"""
    helper = Path(__file__).resolve().parent / 'HardwareAdvisor.exe'
    if not helper.is_file():
        raise ValueError('安装前检测助手缺失，请使用完整的软件包；未修改当前环境。')
    try:
        result = subprocess.run([str(helper), '--json'], capture_output=True, timeout=20,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise ValueError('检测助手未正常完成；不能据此判断显卡不支持，请查看环境检查。')
        report = json.loads(result.stdout.decode('utf-8-sig'))
        if report.get('schema') != 'voice-hardware-report-v1':
            raise ValueError('检测报告格式不兼容，请核对软件版本。')
        return report
    except subprocess.TimeoutExpired as exc:
        raise ValueError('显卡信息读取超时，未作兼容性结论；当前环境未改动。') from exc

