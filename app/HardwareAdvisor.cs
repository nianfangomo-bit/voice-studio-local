using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

// 此助手不依赖 Python，不加载模型，不修改驱动或现有环境。
public class HardwareGpu {
    public string name;
    public string driver;
    public string capability;
    public string memory;
    public string status;
    public string message;
    public string url;
}
public class HardwareReport {
    public string schema = "voice-hardware-report-v1";
    public string rules = "2026-11-17.1";
    public string system;
    public bool x64;
    public string note;
    public List<HardwareGpu> gpus = new List<HardwareGpu>();
}
public static class HardwareAdvisor {
    public const string Upstream = "https://github.com/RVC-Boss/GPT-SoVITS/releases/tag/20250606v2pro";
    public const string Current = "https://github.com/nianfangomo-bit/voice-studio-local/releases";
    public const string Drivers = "https://www.nvidia.com/Download/index.aspx";
    public const long Nvidia50Bytes = 8835144925L;
    public const string Nvidia50Hash = "97b4edcd451c42357db7e26e6c1c877ca5d85144fe97beaff6d7005d35bee008";

    // 驱动阈值是保守的候选筛选基线，不等同包内依赖已验证或最低硬件驱动要求。
    public static HardwareGpu Classify(string name, string driver, string capability, string memory) {
        var g = new HardwareGpu {name=name,driver=driver,capability=capability,memory=memory,status="unknown",url=""};
        Version cap, drv;
        if(!Version.TryParse(capability, out cap) || !Version.TryParse(driver, out drv)) {
            g.message="架构或驱动信息不完整，暂不推荐下载。请更新检测信息或导出报告。"; return g;
        }
        if(cap.Major==12 && cap.Minor==0) {
            if(drv < new Version(570,65)) {
                g.status="driver_review"; g.url=Drivers;
                g.message="驱动低于 CUDA 12.8 GA 的 Windows 配套基线 570.65。请先核对该型号所需驱动，不要先下载环境；助手不会安装驱动。";
            } else {
                g.status="candidate_50"; g.url=Upstream;
                g.message="候选：上游 v2Pro nvidia50 包（8.84 GB）。旧版 CUDA 11.8 环境不适用于已复现的 5060 问题；朋友电脑的手工整合有训练记录，但新版完整安装包仍须单独验收。助手只提供官方来源，不自动安装或切换。";
            }
        } else if((cap.Major==8 && (cap.Minor==0 || cap.Minor==6 || cap.Minor==9)) || (cap.Major==7 && cap.Minor==5)) {
            if(drv < new Version(520,6)) {
                g.status="driver_review"; g.url=Drivers;
                g.message="驱动低于旧环境 CUDA 11.8 GA 的 Windows 配套基线 520.06，先核对驱动；兼容模式可能不同，此处不判定硬件损坏。";
            } else {
                g.status="candidate_legacy"; g.url=Current;
                g.message="可评估发布页中标明适用此类环境的完整离线候选包。已记录 RTX 3080 本机测试，但不能据此保证其他型号/驱动；已有可用安装无需重复下载。请核对包版本并实际验收。";
            }
        } else {
            g.message="此架构没有产品验证规则，暂不推荐安装包；请导出报告供核查。";
        }
        return g;
    }

    // 只调用系统或 NVIDIA 标准目录的检测程序；异步读取输出并限制运行时间。
    static string Query(string exe, string fields) {
        var start = new ProcessStartInfo(exe, "--query-gpu="+fields+" --format=csv,noheader,nounits");
        start.UseShellExecute=false; start.CreateNoWindow=true;
        start.RedirectStandardOutput=true; start.RedirectStandardError=true;
        using(var p=Process.Start(start)) {
            var output=p.StandardOutput.ReadToEndAsync(); var error=p.StandardError.ReadToEndAsync();
            if(!p.WaitForExit(12000)) {try {p.Kill();} catch {} throw new Exception("显卡检测超时，未作兼容性判断。");}
            Task.WaitAll(output,error);
            if(p.ExitCode!=0) throw new Exception("显卡驱动未返回完整检测信息，请检查驱动；未修改环境。");
            return output.Result;
        }
    }

    // 不收集用户名、录音、路径、设备序列号或软件会话令牌。
    public static HardwareReport Detect() {
        var r=new HardwareReport {system=Environment.OSVersion.ToString(), x64=Environment.Is64BitOperatingSystem};
        // 避免未声明系统兼容清单的程序将 Windows 11 报成 Windows 8。
        using(var key=Microsoft.Win32.Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Microsoft\Windows NT\CurrentVersion")) {
            if(key!=null) {
                int build;
                if(int.TryParse(Convert.ToString(key.GetValue("CurrentBuildNumber")),out build))
                    r.system=(build>=22000?"Windows 11":"Windows")+" "+Convert.ToString(key.GetValue("DisplayVersion"))+" (build "+build+"."+Convert.ToString(key.GetValue("UBR"))+")";
            }
        }
        string architecture=Environment.GetEnvironmentVariable("PROCESSOR_ARCHITEW6432") ?? Environment.GetEnvironmentVariable("PROCESSOR_ARCHITECTURE") ?? "";
        if(!r.x64 || architecture.IndexOf("ARM",StringComparison.OrdinalIgnoreCase)>=0) {
            r.note="当前仅提供 Windows x64 候选包，系统架构不匹配；未推荐下载。"; return r;
        }
        var candidates=new [] {Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"nvidia-smi.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),"NVIDIA Corporation","NVSMI","nvidia-smi.exe")};
        string exe=Array.Find(candidates,File.Exists);
        if(exe==null) {r.note="未找到 NVIDIA 检测程序。这不等于没有显卡；请核对驱动。当前产品不能据此承诺 CPU 训练或配音。";return r;}
        try {
            string csv=Query(exe,"name,driver_version,compute_cap,memory.total");
            foreach(string line in csv.Split(new [] {'\r','\n'},StringSplitOptions.RemoveEmptyEntries)) {
                var fields=line.Split(',');
                if(fields.Length!=4) throw new Exception("显卡检测输出格式未知，未推荐下载。");
                r.gpus.Add(Classify(fields[0].Trim(),fields[1].Trim(),fields[2].Trim(),fields[3].Trim()));
            }
            r.note=r.gpus.Count>1 ? "检测到多张显卡：仅逐卡给出候选信息，不自动选择训练设备。" : "硬件检测只筛选候选包，不代表当前运行环境或完整训练已通过。";
            if(r.gpus.Count==0) r.note="未获得显卡记录；请检查驱动，不自动推荐。";
        } catch(Exception ex) {r.gpus.Clear();r.note=ex.Message;}
        return r;
    }

    // 报告只包含本轮检测事实，用户明确选择位置后才导出。
    public static string Json(HardwareReport r) {return new JavaScriptSerializer().Serialize(r);}
    public static string Describe(HardwareReport r) {
        var b=new StringBuilder("系统："+r.system+"\r\n规则版本："+r.rules+"\r\n\r\n"+r.note+"\r\n");
        foreach(var g in r.gpus) b.Append("\r\n显卡："+g.name+"\r\n驱动："+g.driver+"　架构："+g.capability+"　显存："+g.memory+" MiB\r\n"+g.message+"\r\n");
        b.Append("\r\n不会自动下载、安装驱动或覆盖旧环境。离线包校验只证明文件一致，不证明兼容。\r\nCUDA 配套基线来源：docs.nvidia.com/cuda/archive/12.8.0/cuda-toolkit-release-notes/index.html");
        return b.ToString();
    }

    // 暂仅校验已固定摘要的上游 50 系包；不解压未验收归档，不执行其中程序。
    public static bool Verify50(string path) {
        if(new FileInfo(path).Length!=Nvidia50Bytes) return false;
        using(var f=File.OpenRead(path)) using(var h=SHA256.Create())
            return BitConverter.ToString(h.ComputeHash(f)).Replace("-","").ToLowerInvariant()==Nvidia50Hash;
    }

    // 合成案例测试纯规则，不读取或冒充目标显卡实测结果。
    static void SelfTest() {
        if(Classify("RTX 5060 Laptop","591.59","12.0","8151").status!="candidate_50" ||
           Classify("RTX 5060","560.00","12.0","8192").status!="driver_review" ||
           Classify("RTX 3080","591.59","8.6","10240").status!="candidate_legacy" ||
           Classify("RTX 5060","591.59","N/A","8192").status!="unknown" ||
           Classify("Future","600.00","13.0","8192").status!="unknown" ||
           Classify("RTX 3080","bad","8.6","10240").status!="unknown") throw new Exception("规则测试失败");
    }

    [STAThread]
    public static int Main(string[] args) {
        if(args.Length>0 && args[0]=="--json") {using(var output=new StreamWriter(Console.OpenStandardOutput(),new UTF8Encoding(false))) output.WriteLine(Json(Detect()));return 0;}
        if(args.Length>0 && args[0]=="--self-test") {SelfTest();return 0;}
        Application.EnableVisualStyles();
        var form=new Form {Text="声线工作室 · 安装前检测",Width=850,Height=640,StartPosition=FormStartPosition.CenterScreen};
        var reportBox=new TextBox {Multiline=true,ReadOnly=true,ScrollBars=ScrollBars.Vertical,Dock=DockStyle.Fill,Font=new Font("Microsoft YaHei UI",11),Text="点击“检测这台电脑”。不需要安装 Python，不会加载模型。"};
        var bar=new FlowLayoutPanel {Dock=DockStyle.Bottom,Height=100,Padding=new Padding(8)};
        var detect=new Button {Text="检测这台电脑",AutoSize=true};
        var save=new Button {Text="导出检测报告",AutoSize=true,Enabled=false};
        var visit=new Button {Text="查看官方候选包/驱动",AutoSize=true,Enabled=false};
        var verify=new Button {Text="校验已下载的 50 系包",AutoSize=true};
        HardwareReport current=null;
        detect.Click+=async delegate {
            detect.Enabled=false;save.Enabled=false;visit.Enabled=false;reportBox.Text="正在读取显卡和驱动信息…";
            try {current=await Task.Run(()=>Detect());reportBox.Text=Describe(current);save.Enabled=true;visit.Enabled=current.gpus.Count==1 && current.gpus[0].url!="";}
            catch(Exception ex) {reportBox.Text="检测未完成："+ex.Message;} finally {detect.Enabled=true;}
        };
        save.Click+=delegate {using(var d=new SaveFileDialog {Filter="JSON 报告|*.json",FileName="voice-hardware-report.json"}) if(d.ShowDialog()==DialogResult.OK) {
            try {File.WriteAllText(d.FileName,Json(current),new UTF8Encoding(false));} catch(Exception ex) {MessageBox.Show("导出失败："+ex.Message);}
        }};
        visit.Click+=delegate {
            if(current==null || current.gpus.Count!=1) return;
            if(MessageBox.Show("将打开官方页面，不会自动安装。候选环境仍需要实际验证。是否继续？","确认",MessageBoxButtons.YesNo)!=DialogResult.Yes) return;
            try {Process.Start(current.gpus[0].url);} catch(Exception ex) {MessageBox.Show("无法打开浏览器："+ex.Message);}
        };
        verify.Click+=async delegate {using(var d=new OpenFileDialog {Filter="上游 50 系归档|*.7z"}) if(d.ShowDialog()==DialogResult.OK) {
            verify.Enabled=false;
            try {bool ok=await Task.Run(()=>Verify50(d.FileName));MessageBox.Show(ok?"大小和 SHA-256 一致。尚未安装，也不代表模型兼容验收通过。":"校验不一致，请核对下载版本或完整性；原文件未改动。");}
            catch(Exception ex) {MessageBox.Show("校验失败："+ex.Message);} finally {verify.Enabled=true;}
        }};
        bar.Controls.AddRange(new Control[] {detect,save,visit,verify});form.Controls.Add(reportBox);form.Controls.Add(bar);
        Application.Run(form);return 0;
    }
}
