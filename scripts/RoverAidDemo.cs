using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;

internal static class RoverAidDemo
{
    private static string Quote(string value)
    {
        var quoted = new StringBuilder("\"");
        int slashes = 0;
        foreach (char ch in value)
        {
            if (ch == '\\')
            {
                slashes++;
                continue;
            }
            quoted.Append('\\', ch == '"' ? slashes * 2 + 1 : slashes);
            quoted.Append(ch);
            slashes = 0;
        }
        return quoted.Append('\\', slashes * 2).Append('"').ToString();
    }

    private static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string script = Path.Combine(root, "scripts", "start_final_demo.ps1");
        int result = 1;
        try
        {
            string arguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File " + Quote(script);
            if (args.Length > 0) arguments += " " + string.Join(" ", args.Select(Quote));
            string powershell = Path.Combine(Environment.SystemDirectory,
                @"WindowsPowerShell\v1.0\powershell.exe");
            using (var process = Process.Start(new ProcessStartInfo(powershell, arguments) {
                WorkingDirectory = root, UseShellExecute = false
            }))
            {
                process.WaitForExit();
                result = process.ExitCode;
            }
        }
        catch (Exception exc)
        {
            Console.Error.WriteLine(exc.Message);
        }

        if (args.Length == 0 && !Console.IsInputRedirected)
        {
            Console.Write("Press Enter to close...");
            Console.ReadLine();
        }
        return result;
    }
}
