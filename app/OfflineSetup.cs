using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using System.Text.RegularExpressions;

public class Part { public string name; public long bytes; public string sha256; }
public class Package { public string version; public string folder; public string archive_sha256; public long unpacked_bytes; public List<Part> parts; }

public static class OfflineInstaller {
    public static readonly string Source = AppDomain.CurrentDomain.BaseDirectory;
    public static Package ReadPackage() {
        var serializer = new JavaScriptSerializer();
        var p = serializer.Deserialize<Package>(File.ReadAllText(Path.Combine(Source, "offline-package.json")));
        if (p == null || p.parts == null || p.parts.Count == 0 || p.parts.Count > 200 ||
            p.folder == null || !Regex.IsMatch(p.folder, @"^VoiceStudio-[0-9][A-Za-z0-9.\-]*-win64-offline$") ||
            p.archive_sha256 == null || !Regex.IsMatch(p.archive_sha256, @"\A[0-9a-fA-F]{64}\z") ||
            p.unpacked_bytes <= 0 || p.unpacked_bytes > 100L*1024*1024*1024)
            throw new Exception("安装清单不完整，请重新下载同一版本的安装文件。");
        var partNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var part in p.parts)
            if (part == null || part.name == null || !Regex.IsMatch(part.name, @"\AVoiceStudio-[0-9][A-Za-z0-9.\-]*-win64-offline\.zip\.part[0-9]{3}\z") ||
                !partNames.Add(part.name) || part.bytes <= 0 || part.bytes > 2L*1024*1024*1024 ||
                part.sha256 == null || !Regex.IsMatch(part.sha256, @"\A[0-9a-fA-F]{64}\z"))
                throw new Exception("安装分卷清单无效。");
        return p;
    }
    static string Hash(string path) {
        using(var stream = File.OpenRead(path)) using(var sha = SHA256.Create())
            return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
    }
    public static void Verify(Package p, Action<string,int> report) {
        for (int i=0; i<p.parts.Count; i++) {
            var part = p.parts[i]; var path=Path.Combine(Source, part.name);
            report("校验分卷 " + (i+1) + " / " + p.parts.Count, i*100/p.parts.Count);
            if (!File.Exists(path) || new FileInfo(path).Length != part.bytes)
                throw new Exception("缺少或未下载完整："+part.name+"。请把所有分卷和安装程序放在同一文件夹。");
            if (!String.Equals(Hash(path), part.sha256, StringComparison.OrdinalIgnoreCase))
                throw new Exception("校验不一致："+part.name+"。请重新下载该分卷，已有训练数据未改动。");
        }
    }
    public static string Install(Package p, string destination, Action<string,int> report) {
        destination=Path.GetFullPath(destination.Trim());
        if(Directory.Exists(destination) || File.Exists(destination))
            throw new Exception("安装目录已经存在，请选择一个新的空目录，以保留已有声线和作品。");
        var parent=Directory.GetParent(destination);
        if(parent==null || !parent.Exists) throw new Exception("请选择已有磁盘中的安装位置。");
        var tar=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"tar.exe");
        if(!File.Exists(tar)) throw new Exception("当前 Windows 缺少 tar.exe。请使用 Windows 10 更新版或 Windows 11；可以改用完整 ZIP 手动解压。");
        var permissionProbe=Path.Combine(parent.FullName,".voice-studio-write-check-"+Guid.NewGuid().ToString("N"));
        try { using(File.Create(permissionProbe)) {} File.Delete(permissionProbe); }
        catch(UnauthorizedAccessException) {throw new Exception("此目录不可写。请选择当前用户有权限的新目录，例如 D 盘个人文件夹；已有安装未改动。");}
        long packed=0; foreach(var part in p.parts) packed+=part.bytes;
        if(new DriveInfo(Path.GetPathRoot(destination)).AvailableFreeSpace < packed+p.unpacked_bytes+1024L*1024*1024)
            throw new Exception("剩余空间不足，安装时需要分卷合并和解压空间。建议至少预留 30 GB。");
        Verify(p, delegate(string text,int percent) {report(text,percent/4);});
        var stage=Path.Combine(parent.FullName,".voice-studio-install-"+Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(stage);
        var joined=Path.Combine(stage,"payload.zip");
        report("合并安装分卷",25);
        long copied=0;
        using(var target=File.Create(joined)) {
            byte[] buffer=new byte[1024*1024];
            foreach(var part in p.parts) using(var source=File.OpenRead(Path.Combine(Source,part.name))) {
                int count;
                while((count=source.Read(buffer,0,buffer.Length))>0) {
                    target.Write(buffer,0,count);copied+=count;
                    report("合并分卷 "+(copied/1024/1024)+" / "+(packed/1024/1024)+" MB",25+(int)(copied*20/packed));
                }
            }
        }
        report("校验完整压缩包",46);
        if(!String.Equals(Hash(joined),p.archive_sha256,StringComparison.OrdinalIgnoreCase))
            throw new Exception("完整压缩包校验失败，安装已停止；请重新下载分卷。暂存位置："+stage);
        long unpacked=0;
        var entryNames=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        using(var archive=ZipFile.OpenRead(joined)) {
            foreach(var entry in archive.Entries) {
                string normalized=entry.FullName.Replace('\\','/');
                if(!normalized.StartsWith(p.folder+"/",StringComparison.Ordinal) || normalized.Contains("../") || normalized.Contains(":"))
                    throw new Exception("压缩包路径异常，停止安装。");
                foreach(var component in normalized.Split('/'))
                    if(component==".." || component=="." || component.EndsWith(".") || component.EndsWith(" "))
                        throw new Exception("压缩包包含不安全路径，停止安装。");
                if(!entryNames.Add(normalized.TrimEnd('/')) || ((entry.ExternalAttributes >> 16) & 0xF000)==0xA000)
                    throw new Exception("压缩包包含重复路径或符号链接，停止安装。");
                unpacked+=entry.Length;
            }
        }
        if(unpacked>p.unpacked_bytes+1024*1024) throw new Exception("解压大小与清单不一致。");
        var extract=Path.Combine(stage,"unpacked");Directory.CreateDirectory(extract);
        report("正在解压运行环境和模型（约 10 GB，请稍候）",50);
        var start=new ProcessStartInfo(tar,"-xf \""+joined+"\" -C \""+extract+"\"");
        start.UseShellExecute=false;start.CreateNoWindow=true;start.RedirectStandardError=true;
        using(var process=Process.Start(start)) {
            var error=process.StandardError.ReadToEnd();process.WaitForExit();
            if(process.ExitCode!=0) throw new Exception("解压失败，已有安装不受影响。请检查空间和目录权限。\n"+error+"\n暂存位置："+stage);
        }
        var application=Path.Combine(extract,p.folder);
        if(!File.Exists(Path.Combine(application,"engine/runtime/python.exe")) || !File.Exists(Path.Combine(application,"Start.cmd")))
            throw new Exception("解压结果不完整，未创建安装目录。暂存位置："+stage);
        Directory.Move(application,destination);
        // Only these installer-created, now empty/temp paths are removed.
        File.Delete(joined);Directory.Delete(extract);Directory.Delete(stage);
        report("安装完成，可以开始整理录音和训练声线",100);
        return destination;
    }
    [STAThread] public static void Main(string[] args) {
        try {
            var package=ReadPackage();
            if(args.Length>0 && args[0]=="--verify-only") {Verify(package,(t,n)=>Console.WriteLine(t));Environment.Exit(0);}
            if(args.Length==2 && args[0]=="--install-to") {Install(package,args[1],(t,n)=>Console.WriteLine(t));Environment.Exit(0);}
            Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new InstallerForm(package));
        } catch(Exception ex) {
            if(args.Length>0) {Console.Error.WriteLine(ex.Message);Environment.Exit(1);}
            else MessageBox.Show(ex.Message,"声线工作室离线安装",MessageBoxButtons.OK,MessageBoxIcon.Error);
        }
    }
}

public class InstallerForm : Form {
    Package package; TextBox path; Button install,browse,launch; Label status; ProgressBar progress; string installed;
    public InstallerForm(Package p) {
        package=p; Text="声线工作室 · 完整离线安装";ClientSize=new Size(620,340);
        FormBorderStyle=FormBorderStyle.FixedDialog;MaximizeBox=false;StartPosition=FormStartPosition.CenterScreen;
        Font=new Font("Microsoft YaHei UI",10F);
        var title=new Label {Text="声线工作室  "+p.version,Font=new Font(Font.FontFamily,18,FontStyle.Bold),AutoSize=true,Location=new Point(24,22)};
        Controls.Add(title);
        Controls.Add(new Label {Text="包含运行环境、GPT-SoVITS 和基础模型，全程离线安装。\n训练与配音需要兼容的 NVIDIA 显卡驱动。",Location=new Point(24,70),Size=new Size(570,52)});
        Controls.Add(new Label {Text="安装位置（请选择新的目录）",Location=new Point(24,132),AutoSize=true});
        path=new TextBox {Text=Path.Combine(OfflineInstaller.Source,"VoiceStudio-"+p.version),Location=new Point(24,158),Size=new Size(465,28)};
        Controls.Add(path);
        browse=new Button {Text="选择…",Location=new Point(500,157),Size=new Size(95,30)};
        browse.Click+=delegate {using(var dialog=new FolderBrowserDialog()) {dialog.Description="选择安装父目录";if(dialog.ShowDialog()==DialogResult.OK)path.Text=Path.Combine(dialog.SelectedPath,"VoiceStudio-"+package.version);}};
        Controls.Add(browse);
        progress=new ProgressBar {Location=new Point(24,204),Size=new Size(571,18)};Controls.Add(progress);
        status=new Label {Text="将所有分卷和安装程序放在同一文件夹后开始。",Location=new Point(24,232),Size=new Size(570,32)};Controls.Add(status);
        install=new Button {Text="开始离线安装",Location=new Point(24,282),Size=new Size(170,36)};
        launch=new Button {Text="打开声线工作室",Location=new Point(210,282),Size=new Size(170,36),Enabled=false};
        launch.Click+=delegate {Process.Start(new ProcessStartInfo(Path.Combine(installed,"Start.cmd")){WorkingDirectory=installed,UseShellExecute=true});};
        Controls.Add(install);Controls.Add(launch);
        install.Click+=async delegate {
            install.Enabled=false;browse.Enabled=false;path.Enabled=false;
            string selected=path.Text;
            try {
                installed=await Task.Run(()=>OfflineInstaller.Install(package,selected,(text,percent)=> {
                    if(!IsDisposed)Invoke((Action)delegate {status.Text=text;progress.Value=Math.Max(0,Math.Min(100,percent));});
                }));
                launch.Enabled=true;install.Text="已完成";
            } catch(Exception ex) {MessageBox.Show(ex.Message,"安装未完成",MessageBoxButtons.OK,MessageBoxIcon.Warning);install.Enabled=true;browse.Enabled=true;path.Enabled=true;status.Text="已停止，可检查提示后重试。";}
        };
        FormClosing+=delegate(object sender,FormClosingEventArgs e) {if(!install.Enabled&&!launch.Enabled) {e.Cancel=true;MessageBox.Show("正在安装，请等待完成。",Text);}};
    }
}
