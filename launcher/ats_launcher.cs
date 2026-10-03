// Double-click launcher for the CV Pre-Screen ATS app.
// It does the same as typing `.\.venv\Scripts\streamlit.exe run app.py` in
// PowerShell. Build it with launcher\build.ps1; ats.exe must sit in the
// project folder, next to app.py and .venv.

using System;
using System.Diagnostics;
using System.IO;

class AtsLauncher
{
    static int Main()
    {
        // The folder ats.exe is in, so the launcher still works if the
        // project folder is moved or renamed.
        string projectFolder = AppDomain.CurrentDomain.BaseDirectory;
        string streamlit = Path.Combine(projectFolder, @".venv\Scripts\streamlit.exe");
        string app = Path.Combine(projectFolder, "app.py");

        if (!File.Exists(streamlit))
        {
            return Fail(
                "Could not find .venv\\Scripts\\streamlit.exe next to ats.exe.\n" +
                "Set up the virtual environment first (see README.md).");
        }
        if (!File.Exists(app))
        {
            return Fail("Could not find app.py next to ats.exe.");
        }

        Console.Title = "CV Pre-Screen ATS";
        Console.WriteLine("Starting CV Pre-Screen ATS. Your browser will open in a few seconds.");
        Console.WriteLine("To stop the app, close this window (or press Ctrl+C).");
        Console.WriteLine();

        ProcessStartInfo startInfo = new ProcessStartInfo(streamlit, "run \"" + app + "\"");
        // Run from the project folder so Streamlit reads .streamlit\config.toml
        // (localhost only, upload limit, no error details in the browser).
        startInfo.WorkingDirectory = projectFolder;
        // Share this console window, so Streamlit's messages appear here and
        // closing the window also stops the app.
        startInfo.UseShellExecute = false;

        using (Process process = Process.Start(startInfo))
        {
            process.WaitForExit();
            if (process.ExitCode != 0)
            {
                return Fail(
                    "The app stopped with an error (code " + process.ExitCode + "). " +
                    "See the messages above.");
            }
        }
        return 0;
    }

    // Keeps the window open so the message can be read before it closes.
    static int Fail(string message)
    {
        Console.WriteLine();
        Console.WriteLine(message);
        Console.WriteLine("Press any key to close this window.");
        Console.ReadKey(true);
        return 1;
    }
}
