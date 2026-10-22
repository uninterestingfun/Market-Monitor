// 市场监测 · Windows 安装程序（原生 .NET WinForms，无临时解压）
// 编译：csc /target:winexe /codepage:65001 /win32icon:assets\icon.ico
//        /resource:installer\payload.zip,payload.zip /out:dist\MarketMonitor-Setup.exe installer\Installer.cs
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

namespace MarketMonitorSetup
{
    internal static class Program
    {
        internal const string AppName = "市场监测";
        internal const string AppId = "MarketMonitor";
        internal const string AppVersion = "1.0.0";
        internal const string ExeName = "MarketMonitor.exe";
        internal const string ShortcutName = "市场监测.lnk";
        internal const string PayloadResource = "payload.zip";

        /// <summary>提权重启后自动开始安装。</summary>
        internal static bool AutoStart;

        [STAThread]
        private static int Main(string[] args)
        {
            bool silent = Has(args, "--silent");
            string dir = Value(args, "--dir");
            bool noDesktop = Has(args, "--no-desktop");
            bool noStart = Has(args, "--no-startmenu");
            bool noLaunch = Has(args, "--no-launch");
            string logPath = Value(args, "--log");

            if (string.IsNullOrEmpty(dir)) dir = DefaultDir();

            if (!silent)
            {
                AutoStart = Has(args, "--auto");
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new InstallerForm());
                return 0;
            }

            try
            {
                var lines = new List<string>();
                Install(dir, !noDesktop, !noStart, !noLaunch, null, delegate(string m) { lines.Add(m); });
                lines.Add("OK " + dir);
                if (!string.IsNullOrEmpty(logPath))
                    File.WriteAllText(logPath, string.Join("\r\n", lines.ToArray()), System.Text.Encoding.UTF8);
                return 0;
            }
            catch (Exception ex)
            {
                if (!string.IsNullOrEmpty(logPath))
                    File.WriteAllText(logPath, "ERROR " + ex.Message, System.Text.Encoding.UTF8);
                return 1;
            }
        }

        internal static string DefaultDir()
        {
            string baseDir = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            return Path.Combine(baseDir, Path.Combine("Programs", AppId));
        }

        /// <summary>当前进程是否已提权。</summary>
        internal static bool IsAdmin()
        {
            try
            {
                using (WindowsIdentity identity = WindowsIdentity.GetCurrent())
                    return new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator);
            }
            catch { return false; }
        }

        /// <summary>目标目录是否可写（会顺带创建目录），失败时给出具体原因。</summary>
        internal static bool CanWrite(string dir, out string reason)
        {
            reason = null;
            for (int attempt = 0; attempt < 2; attempt++)
            {
                try
                {
                    Directory.CreateDirectory(dir);
                    string probe = Path.Combine(dir, ".mm-probe-" + Guid.NewGuid().ToString("N") + ".tmp");
                    // DeleteOnClose：不依赖后续 File.Delete（杀软扫描时可能短暂占用）
                    using (var fs = new FileStream(probe, FileMode.CreateNew, FileAccess.Write,
                                                   FileShare.None, 4096, FileOptions.DeleteOnClose))
                    {
                        fs.WriteByte(0x41);
                    }
                    reason = null;
                    return true;
                }
                catch (Exception ex)
                {
                    reason = ex.Message;
                    if (attempt == 0) Thread.Sleep(150);
                }
            }
            return false;
        }

        internal static bool CanWrite(string dir)
        {
            string reason;
            return CanWrite(dir, out reason);
        }

        [System.Runtime.InteropServices.DllImport("kernel32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
        private static extern bool GetVolumeInformation(
            string rootPathName, StringBuilder volumeNameBuffer, int volumeNameSize,
            out uint volumeSerialNumber, out uint maximumComponentLength, out uint fileSystemFlags,
            StringBuilder fileSystemNameBuffer, int fileSystemNameSize);

        /// <summary>目标卷是否被标记为只读（FILE_READ_ONLY_VOLUME）。</summary>
        internal static bool IsReadOnlyVolume(string path)
        {
            try
            {
                string root = Path.GetPathRoot(Path.GetFullPath(path));
                if (string.IsNullOrEmpty(root)) return false;
                uint serial, maxLen, flags;
                if (GetVolumeInformation(root, new StringBuilder(260), 260, out serial, out maxLen, out flags,
                                         new StringBuilder(260), 260))
                    return (flags & 0x00080000u) != 0;
            }
            catch { }
            return false;
        }

        /// <summary>以管理员身份重启安装器（UAC 提权），安装到受保护目录时使用。</summary>
        internal static bool RelaunchElevated(string target, bool desktop, bool startmenu, bool launch)
        {
            string args = "--elevated --dir \"" + target + "\"";
            if (!desktop) args += " --no-desktop";
            if (!startmenu) args += " --no-startmenu";
            if (!launch) args += " --no-launch";
            try
            {
                var info = new ProcessStartInfo(Application.ExecutablePath, args + " --auto");
                info.Verb = "runas";
                info.UseShellExecute = true;
                Process.Start(info);
                return true;
            }
            catch (Win32Exception ex)
            {
                // 1223 = 用户取消了 UAC
                if (ex.NativeErrorCode != 1223)
                    MessageBox.Show("提权失败：" + ex.Message, "无法提升权限", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return false;
            }
        }

        internal static bool Has(string[] args, string key)
        {
            return Array.IndexOf(args, key) >= 0;
        }

        internal static string Value(string[] args, string key)
        {
            int i = Array.IndexOf(args, key);
            if (i >= 0 && i + 1 < args.Length) return args[i + 1];
            return null;
        }

        /// <summary>把内嵌的 payload.zip 解压到目标目录，并写入卸载脚本/快捷方式/注册表。</summary>
        internal static void Install(string target, bool desktop, bool startmenu, bool launch,
                                     Action<double, string> progress, Action<string> log)
        {
            target = Path.GetFullPath(target);
            if (target.Length < 4) throw new Exception("安装目录无效");

            if (Directory.Exists(target))
            {
                string[] items = Directory.GetFileSystemEntries(target);
                if (items.Length > 0 && !File.Exists(Path.Combine(target, ExeName)))
                    throw new Exception("目标目录非空，请选择空目录：\n" + target);
            }

            Report(progress, log, 0.03, "检查安装目录…");
            if (File.Exists(target))
                throw new Exception("目标路径是一个文件，不是目录：\n" + target);
            string writeError;
            if (!CanWrite(target, out writeError))
                throw new Exception(
                    "无法写入目标目录：\n" + target + "\n\n" +
                    "系统返回：" + writeError + "\n\n" +
                    "若确实需要管理员权限（例如 Program Files），请右键安装程序选择「以管理员身份运行」；\n" +
                    "否则请换一个有写权限的目录。");
            Report(progress, log, 0.04, "创建安装目录…");
            Directory.CreateDirectory(target);

            Assembly asm = Assembly.GetExecutingAssembly();
            using (Stream stream = asm.GetManifestResourceStream(PayloadResource))
            {
                if (stream == null) throw new Exception("安装包损坏：缺少内嵌 payload.zip");
                using (var zip = new ZipArchive(stream, ZipArchiveMode.Read))
                {
                    int total = zip.Entries.Count, done = 0;
                    foreach (ZipArchiveEntry entry in zip.Entries)
                    {
                        string name = entry.FullName.Replace('/', Path.DirectorySeparatorChar);
                        string dest = Path.Combine(target, name);
                        if (string.IsNullOrEmpty(entry.Name))
                        {
                            Directory.CreateDirectory(dest);
                            continue;
                        }
                        Directory.CreateDirectory(Path.GetDirectoryName(dest));
                        entry.ExtractToFile(dest, true);
                        done++;
                        if (done % 40 == 0)
                            Report(progress, log, 0.05 + 0.65 * done / Math.Max(total, 1), "解压程序文件…");
                    }
                }
            }

            Report(progress, log, 0.74, "写入卸载程序…");
            WriteUninstaller(target);

            string exe = Path.Combine(target, ExeName);
            if (desktop)
            {
                try
                {
                    Report(progress, log, 0.82, "创建桌面快捷方式…");
                    string desktopDir = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
                    if (string.IsNullOrEmpty(desktopDir)) throw new Exception("无法定位桌面目录");
                    string link = Path.Combine(desktopDir, ShortcutName);
                    CreateShortcut(link, exe, target);
                    Report(progress, log, 0.83, "桌面快捷方式：" + link);
                }
                catch (Exception ex) { if (log != null) log("跳过桌面快捷方式：" + ex.Message); }
            }
            if (startmenu)
            {
                try
                {
                    Report(progress, log, 0.87, "创建开始菜单项…");
                    string programs = Environment.GetFolderPath(Environment.SpecialFolder.Programs);
                    if (string.IsNullOrEmpty(programs)) throw new Exception("无法定位开始菜单目录");
                    string link = Path.Combine(programs, ShortcutName);
                    CreateShortcut(link, exe, target);
                    Report(progress, log, 0.88, "开始菜单项：" + link);
                }
                catch (Exception ex) { if (log != null) log("跳过开始菜单项：" + ex.Message); }
            }

            try
            {
                Report(progress, log, 0.93, "写入卸载信息…");
                WriteRegistry(target, exe);
            }
            catch (Exception ex) { if (log != null) log("跳过卸载信息（无注册表权限）：" + ex.Message); }

            if (launch)
            {
                Report(progress, log, 0.97, "启动程序…");
                try { Process.Start(new ProcessStartInfo(exe) { WorkingDirectory = target }); }
                catch (Exception ex) { if (log != null) log("启动失败：" + ex.Message); }
            }
            Report(progress, log, 1.0, "完成");
        }

        private static void Report(Action<double, string> progress, Action<string> log, double ratio, string message)
        {
            if (log != null) log(message);
            if (progress != null) progress(ratio, message);
        }

        private static void WriteUninstaller(string target)
        {
            string bat =
                "@echo off\r\n" +
                "chcp 65001 >nul\r\n" +
                "title 卸载 " + AppName + "\r\n" +
                "echo.\r\n" +
                "echo   即将卸载「" + AppName + "」，程序目录与本地数据都会被删除。\r\n" +
                "echo   目录：%~dp0\r\n" +
                "echo.\r\n" +
                "choice /c YN /m \"确认卸载\"\r\n" +
                "if errorlevel 2 exit /b 1\r\n" +
                "taskkill /f /im " + ExeName + " >nul 2>&1\r\n" +
                "timeout /t 1 >nul\r\n" +
                "del \"%USERPROFILE%\\Desktop\\" + ShortcutName + "\" >nul 2>&1\r\n" +
                "del \"%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\" + ShortcutName + "\" >nul 2>&1\r\n" +
                "reg delete \"HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\" + AppId + "\" /f >nul 2>&1\r\n" +
                "cd /d \"%TEMP%\"\r\n" +
                "start \"\" cmd /c \"timeout /t 2 >nul & rmdir /s /q \"\"%~dp0\"\" \"\r\n" +
                "exit /b 0\r\n";
            File.WriteAllText(Path.Combine(target, "uninstall.bat"), bat, new System.Text.UTF8Encoding(true));
        }

        private static void CreateShortcut(string linkPath, string targetPath, string workDir)
        {
            Type shellType = Type.GetTypeFromProgID("WScript.Shell");
            if (shellType == null) throw new Exception("系统未注册 WScript.Shell");
            dynamic shell = Activator.CreateInstance(shellType);
            dynamic shortcut = shell.CreateShortcut(linkPath);
            shortcut.TargetPath = targetPath;
            shortcut.WorkingDirectory = workDir;
            shortcut.IconLocation = targetPath + ",0";
            shortcut.Description = AppName;
            shortcut.Save();
        }

        private static void WriteRegistry(string target, string exe)
        {
            using (RegistryKey key = Registry.CurrentUser.CreateSubKey(
                @"Software\Microsoft\Windows\CurrentVersion\Uninstall\" + AppId))
            {
                key.SetValue("DisplayName", AppName);
                key.SetValue("DisplayVersion", AppVersion);
                key.SetValue("Publisher", "uninterestingfun");
                key.SetValue("InstallLocation", target);
                key.SetValue("DisplayIcon", exe);
                key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                key.SetValue("UninstallString", "\"" + Path.Combine(target, "uninstall.bat") + "\"");
            }
        }
    }

    internal sealed class InstallerForm : Form
    {
        private readonly TextBox _path = new TextBox();
        private readonly CheckBox _desktop = new CheckBox();
        private readonly CheckBox _start = new CheckBox();
        private readonly CheckBox _launch = new CheckBox();
        private readonly ProgressBar _bar = new ProgressBar();
        private readonly Label _status = new Label();
        private readonly Button _install = new Button();
        private readonly Button _browse = new Button();
        private bool _busy;
        private bool _fallbackTried;

        internal InstallerForm()
        {
            Text = Program.AppName + " 安装程序";
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(560, 372);
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            Font = new Font("Microsoft YaHei UI", 9f);
            BackColor = Color.FromArgb(245, 247, 250);
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }

            var header = new Panel { Dock = DockStyle.Top, Height = 76, BackColor = Color.FromArgb(11, 18, 32) };
            header.Controls.Add(new Label
            {
                Text = Program.AppName,
                ForeColor = Color.FromArgb(233, 238, 248),
                Font = new Font("Microsoft YaHei UI", 15f, FontStyle.Bold),
                AutoSize = true,
                Location = new Point(18, 14)
            });
            header.Controls.Add(new Label
            {
                Text = "版本 " + Program.AppVersion + " · 本地行情监测面板",
                ForeColor = Color.FromArgb(127, 140, 163),
                AutoSize = true,
                Location = new Point(20, 46)
            });
            Controls.Add(header);

            var lbl = new Label { Text = "安装目录", Location = new Point(20, 96), AutoSize = true };
            Controls.Add(lbl);

            _path.Location = new Point(20, 118);
            _path.Width = 380;
            _path.Text = Program.DefaultDir();
            Controls.Add(_path);

            _browse.Text = "浏览…";
            _browse.Location = new Point(408, 116);
            _browse.Size = new Size(70, 26);
            _browse.Click += delegate
            {
                using (var dialog = new FolderBrowserDialog())
                {
                    dialog.Description = "选择安装目录";
                    dialog.SelectedPath = _path.Text;
                    if (dialog.ShowDialog(this) == DialogResult.OK)
                    {
                        string chosen = dialog.SelectedPath;
                        if (string.Equals(Path.GetFileName(chosen), Program.AppId, StringComparison.OrdinalIgnoreCase))
                            _path.Text = chosen;
                        else
                            _path.Text = Path.Combine(chosen, Program.AppId);
                    }
                }
            };
            Controls.Add(_browse);

            var test = new Button { Text = "检测", Location = new Point(486, 116), Size = new Size(56, 26) };
            test.Click += delegate
            {
                string target = _path.Text.Trim();
                string reason;
                bool ok = Program.CanWrite(target, out reason);
                var sb = new StringBuilder();
                sb.AppendLine("目标路径：" + target);
                sb.AppendLine("当前用户：" + Environment.UserName + (Program.IsAdmin() ? "（管理员）" : "（普通用户）"));
                sb.AppendLine("目录存在：" + (Directory.Exists(target) ? "是" : "否"));
                sb.AppendLine("卷状态：" + (Program.IsReadOnlyVolume(target) ? "只读卷（Read-only）" : "可写卷"));
                sb.AppendLine("写入测试：" + (ok ? "通过" : "失败"));
                if (!ok)
                {
                    sb.AppendLine("系统返回：" + reason);
                    sb.AppendLine();
                    if (Program.IsReadOnlyVolume(target))
                        sb.AppendLine("该磁盘被标记为只读，需要先在「磁盘管理」或 diskpart 里清除只读属性。");
                    else
                        sb.AppendLine("建议改用：" + Program.DefaultDir());
                }
                MessageBox.Show(this, sb.ToString(), "目录检测",
                                MessageBoxButtons.OK, ok ? MessageBoxIcon.Information : MessageBoxIcon.Warning);
            };
            Controls.Add(test);

            _desktop.Text = "创建桌面快捷方式";
            _desktop.Location = new Point(20, 158);
            _desktop.AutoSize = true;
            _desktop.Checked = true;
            Controls.Add(_desktop);

            _start.Text = "创建开始菜单项";
            _start.Location = new Point(20, 184);
            _start.AutoSize = true;
            _start.Checked = true;
            Controls.Add(_start);

            _launch.Text = "安装完成后立即启动";
            _launch.Location = new Point(20, 210);
            _launch.AutoSize = true;
            _launch.Checked = true;
            Controls.Add(_launch);

            var note = new Label
            {
                Text = "数据（行情库、导出、日志）会写在程序的 data 目录，卸载时一并删除。",
                ForeColor = Color.FromArgb(107, 118, 136),
                Location = new Point(20, 240),
                Size = new Size(510, 20)
            };
            Controls.Add(note);

            _bar.Location = new Point(20, 276);
            _bar.Size = new Size(518, 14);
            _bar.Minimum = 0;
            _bar.Maximum = 100;
            Controls.Add(_bar);

            _status.Text = "准备就绪";
            _status.ForeColor = Color.FromArgb(90, 100, 116);
            _status.Location = new Point(20, 296);
            _status.AutoSize = true;
            Controls.Add(_status);

            _install.Text = "安装";
            _install.Location = new Point(376, 324);
            _install.Size = new Size(78, 30);
            _install.Click += OnInstall;
            Controls.Add(_install);

            var cancel = new Button { Text = "取消", Location = new Point(462, 324), Size = new Size(76, 30) };
            cancel.Click += delegate { Close(); };
            Controls.Add(cancel);

            AcceptButton = _install;

            Shown += delegate
            {
                if (Program.AutoStart)
                    BeginInvoke(new Action(delegate { OnInstall(null, EventArgs.Empty); }));
            };
        }

        private void OnInstall(object sender, EventArgs e)
        {
            if (_busy) return;
            string target = _path.Text.Trim();
            if (target.Length < 4)
            {
                MessageBox.Show(this, "请选择有效的安装目录。", "路径无效", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            // 提前检查写权限：受保护目录（如 Program Files）直接提示，可一键提权
            string writeReason;
            if (File.Exists(target))
            {
                MessageBox.Show(this, "该路径是一个文件，不是目录：\n" + target + "\n\n请换一个目录。",
                                "路径无效", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            if (!Program.CanWrite(target, out writeReason))
            {
                bool permissionIssue = writeReason != null &&
                    (writeReason.IndexOf("拒绝", StringComparison.Ordinal) >= 0 ||
                     writeReason.IndexOf("denied", StringComparison.OrdinalIgnoreCase) >= 0 ||
                     writeReason.IndexOf("Access", StringComparison.OrdinalIgnoreCase) >= 0);
                string fallback = Program.DefaultDir();

                if (permissionIssue && !Program.IsAdmin())
                {
                    DialogResult choice = MessageBox.Show(this,
                        "无法写入：\n" + target + "\n\n系统返回：" + writeReason + "\n\n" +
                        "【是】以管理员身份重新启动安装程序\n" +
                        "【否】改用推荐目录：" + fallback + "\n" +
                        "【取消】返回，手动修改路径",
                        "目录不可写", MessageBoxButtons.YesNoCancel, MessageBoxIcon.Warning);
                    if (choice == DialogResult.Yes)
                    {
                        if (Program.RelaunchElevated(target, _desktop.Checked, _start.Checked, _launch.Checked))
                            Close();
                    }
                    else if (choice == DialogResult.No && !_fallbackTried)
                    {
                        _fallbackTried = true;
                        _path.Text = fallback;
                        OnInstall(null, EventArgs.Empty);
                    }
                    return;
                }

                DialogResult retry = MessageBox.Show(this,
                    "无法写入：\n" + target + "\n\n系统返回：" + writeReason + "\n\n" +
                    "是否改用推荐目录？\n" + fallback,
                    "目录不可写", MessageBoxButtons.YesNo, MessageBoxIcon.Error);
                if (retry == DialogResult.Yes && !_fallbackTried)
                {
                    _fallbackTried = true;
                    _path.Text = fallback;
                    OnInstall(null, EventArgs.Empty);
                }
                return;
            }

            _busy = true;
            _install.Enabled = false;
            _browse.Enabled = false;
            _status.Text = "开始安装…";

            var thread = new Thread(delegate()
            {
                try
                {
                    Program.Install(target, _desktop.Checked, _start.Checked, _launch.Checked,
                        delegate(double ratio, string message)
                        {
                            BeginInvoke(new Action(delegate
                            {
                                _bar.Value = Math.Min(100, (int)(ratio * 100));
                                _status.Text = message;
                            }));
                        },
                        null);
                    BeginInvoke(new Action(delegate { Finish(true, target, null); }));
                }
                catch (Exception ex)
                {
                    BeginInvoke(new Action(delegate { Finish(false, target, ex.Message); }));
                }
            });
            thread.IsBackground = true;
            thread.Start();
        }

        private void Finish(bool ok, string target, string error)
        {
            _busy = false;
            if (ok)
            {
                _bar.Value = 100;
                _status.Text = "安装完成";
                if (MessageBox.Show(this, "已安装到：\n" + target + "\n\n是否打开安装目录？", "安装完成",
                                    MessageBoxButtons.YesNo, MessageBoxIcon.Information) == DialogResult.Yes)
                    Process.Start("explorer.exe", "\"" + target + "\"");
                Close();
            }
            else
            {
                _install.Enabled = true;
                _browse.Enabled = true;
                _status.Text = "安装失败";
                MessageBox.Show(this, error, "安装失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }
    }
}
