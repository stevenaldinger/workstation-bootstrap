// A stand-in Windows command. Windows starts a command by file name only when it is an
// executable, so each fake (op.exe, gh.exe, wsl.exe, ...) is a copy of this program. It reads its
// own file name to learn which tool it stands for, then runs
//
//     <FAKE_PYTHON> <FAKE_TOOLS_PY> <tool> <arguments>
//
// so the fake behavior lives in tests/fake_tools.py for every platform. Standard input, output,
// and error are inherited, so a caller that closes standard input is seen by the fake, and the
// exit status is the fake's own.
using System;
using System.Diagnostics;
using System.IO;
using System.Text;

internal static class FakeExe
{
    private static string Quote(string argument)
    {
        // Quoting rules of CommandLineToArgvW, which Python's runtime uses to read its arguments.
        if (argument.Length > 0 && argument.IndexOfAny(new[] { ' ', '\t', '"' }) < 0)
        {
            return argument;
        }
        var quoted = new StringBuilder("\"");
        var backslashes = 0;
        foreach (var character in argument)
        {
            if (character == '\\')
            {
                backslashes++;
                continue;
            }
            if (character == '"')
            {
                quoted.Append('\\', backslashes * 2 + 1);
                quoted.Append('"');
            }
            else
            {
                quoted.Append('\\', backslashes);
                quoted.Append(character);
            }
            backslashes = 0;
        }
        quoted.Append('\\', backslashes * 2);
        quoted.Append('"');
        return quoted.ToString();
    }

    private static int Main(string[] args)
    {
        var tool = Path.GetFileNameWithoutExtension(Process.GetCurrentProcess().MainModule.FileName);
        var python = Environment.GetEnvironmentVariable("FAKE_PYTHON");
        var script = Environment.GetEnvironmentVariable("FAKE_TOOLS_PY");
        if (string.IsNullOrEmpty(python) || string.IsNullOrEmpty(script))
        {
            Console.Error.WriteLine("fake " + tool + ": FAKE_PYTHON and FAKE_TOOLS_PY must be set");
            return 64;
        }
        var line = new StringBuilder(Quote(script) + " " + Quote(tool));
        foreach (var argument in args)
        {
            line.Append(' ').Append(Quote(argument));
        }
        var start = new ProcessStartInfo(python, line.ToString()) { UseShellExecute = false };
        using (var process = Process.Start(start))
        {
            process.WaitForExit();
            return process.ExitCode;
        }
    }
}
