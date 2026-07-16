[CmdletBinding()]
param(
    [ValidateSet("status", "apply", "restore")]
    [string]$Action = "status",

    [string]$GameExe = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($GameExe)) {
    $GameExe = Join-Path $PSScriptRoot "..\..\..\Shenzhen.exe"
}

$cecilVersion = "0.11.6"
$dependencyRoot = Join-Path $PSScriptRoot ".deps\mono.cecil\$cecilVersion"
$cecilDll = Join-Path $dependencyRoot "lib\net40\Mono.Cecil.dll"

function Install-MonoCecil {
    if (Test-Path -LiteralPath $cecilDll) {
        return
    }

    New-Item -ItemType Directory -Path $dependencyRoot -Force | Out-Null
    $package = Join-Path $dependencyRoot "mono.cecil.$cecilVersion.nupkg"
    $url = "https://api.nuget.org/v3-flatcontainer/mono.cecil/$cecilVersion/mono.cecil.$cecilVersion.nupkg"

    Write-Host "Downloading Mono.Cecil $cecilVersion from NuGet..."
    Invoke-WebRequest -Uri $url -OutFile $package

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($package, $dependencyRoot)

    if (-not (Test-Path -LiteralPath $cecilDll)) {
        throw "Mono.Cecil package did not contain $cecilDll"
    }
}

$resolvedExe = [System.IO.Path]::GetFullPath($GameExe)
if (-not (Test-Path -LiteralPath $resolvedExe -PathType Leaf)) {
    throw "Game executable not found: $resolvedExe"
}

if ($Action -ne "status") {
    $gameProcess = Get-Process -Name Shenzhen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $gameProcess) {
        throw "Shenzhen.exe is running (PID $($gameProcess.Id)). Close it before applying or restoring the patch."
    }
}

Install-MonoCecil
[System.Reflection.Assembly]::LoadFrom($cecilDll) | Out-Null

if (-not ("Shzio.HotReloadPatcher" -as [type])) {
    $patcherSource = @'
using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using Mono.Cecil;
using Mono.Cecil.Cil;

namespace Shzio
{
    public static class HotReloadPatcher
    {
        private const uint RepositoryAccessorToken = 0x060002A4;
        private const uint ReloadMethodToken = 0x060002A5;
        private const uint SolutionListFieldToken = 0x040001EC;
        private const uint SolutionPathsFieldToken = 0x040001ED;
        private const uint BrowserOpenMethodToken = 0x06000B06;

        private const string ExpectedOriginalHash =
            "A0DFE8E1E91B6633C3BA00210762EC0D7E6786AB9C2C7912D9C9AFDD654D98F9";

        public static string BackupPath(string exePath)
        {
            return exePath + ".shzio-hot-reload.original";
        }

        public static string Status(string exePath)
        {
            string hash = Sha256(exePath);
            bool patched;
            try
            {
                patched = HasCompletePatch(exePath);
            }
            catch (Exception ex)
            {
                return "unreadable: " + ex.Message + "\nsha256: " + hash;
            }

            if (patched)
                return "patched\nsha256: " + hash + "\nbackup: " + BackupPath(exePath);
            if (String.Equals(hash, ExpectedOriginalHash, StringComparison.OrdinalIgnoreCase))
                return "original-supported\nsha256: " + hash;
            return "unpatched-unsupported\nsha256: " + hash;
        }

        public static string Apply(string exePath)
        {
            if (HasCompletePatch(exePath))
                return "already patched";

            string currentHash = Sha256(exePath);
            if (!String.Equals(currentHash, ExpectedOriginalHash, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(
                    "Refusing to patch an unknown Shenzhen.exe. Expected " + ExpectedOriginalHash +
                    " but found " + currentHash + ".");
            }

            string backup = BackupPath(exePath);
            if (File.Exists(backup))
            {
                string backupHash = Sha256(backup);
                if (!String.Equals(backupHash, ExpectedOriginalHash, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("Existing backup has an unexpected hash: " + backupHash);
            }
            else
            {
                File.Copy(exePath, backup, false);
            }

            string temporary = exePath + ".shzio-hot-reload.tmp";
            try
            {
                PatchTo(exePath, temporary);
                if (!HasCompletePatch(temporary))
                    throw new InvalidOperationException("Patch verification failed before replacement.");

                File.Replace(temporary, exePath, null);
                if (!HasCompletePatch(exePath))
                    throw new InvalidOperationException("Patch verification failed after replacement.");
            }
            finally
            {
                if (File.Exists(temporary))
                    File.Delete(temporary);
            }

            return "patched\nbackup: " + backup + "\nsha256: " + Sha256(exePath);
        }

        public static string Restore(string exePath)
        {
            string backup = BackupPath(exePath);
            if (!File.Exists(backup))
                throw new FileNotFoundException("Original backup not found.", backup);

            string backupHash = Sha256(backup);
            if (!String.Equals(backupHash, ExpectedOriginalHash, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Refusing to restore an unexpected backup: " + backupHash);

            string temporary = exePath + ".shzio-hot-reload.restore.tmp";
            try
            {
                File.Copy(backup, temporary, true);
                File.Replace(temporary, exePath, null);
            }
            finally
            {
                if (File.Exists(temporary))
                    File.Delete(temporary);
            }

            string restoredHash = Sha256(exePath);
            if (!String.Equals(restoredHash, ExpectedOriginalHash, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Restore verification failed: " + restoredHash);

            return "restored\nsha256: " + restoredHash;
        }

        private static void PatchTo(string sourcePath, string outputPath)
        {
            var resolver = new DefaultAssemblyResolver();
            resolver.AddSearchDirectory(Path.GetDirectoryName(sourcePath));
            var reader = new ReaderParameters { AssemblyResolver = resolver, ReadSymbols = false };

            using (var assembly = AssemblyDefinition.ReadAssembly(sourcePath, reader))
            {
                ModuleDefinition module = assembly.MainModule;
                MethodDefinition getRepository = Method(module, RepositoryAccessorToken);
                MethodDefinition reload = Method(module, ReloadMethodToken);
                FieldDefinition solutions = Field(module, SolutionListFieldToken);
                FieldDefinition paths = Field(module, SolutionPathsFieldToken);
                MethodDefinition browserOpen = Method(module, BrowserOpenMethodToken);

                ValidateOriginalShape(reload, browserOpen);

                var listClear = new MethodReference("Clear", module.TypeSystem.Void, solutions.FieldType)
                {
                    HasThis = true
                };
                var dictionaryClear = new MethodReference("Clear", module.TypeSystem.Void, paths.FieldType)
                {
                    HasThis = true
                };

                ILProcessor reloadIl = reload.Body.GetILProcessor();
                Instruction reloadFirst = reload.Body.Instructions[0];
                reloadIl.InsertBefore(reloadFirst, reloadIl.Create(OpCodes.Call, getRepository));
                reloadIl.InsertBefore(reloadFirst, reloadIl.Create(OpCodes.Ldfld, solutions));
                reloadIl.InsertBefore(reloadFirst, reloadIl.Create(OpCodes.Callvirt, listClear));
                reloadIl.InsertBefore(reloadFirst, reloadIl.Create(OpCodes.Call, getRepository));
                reloadIl.InsertBefore(reloadFirst, reloadIl.Create(OpCodes.Ldfld, paths));
                reloadIl.InsertBefore(reloadFirst, reloadIl.Create(OpCodes.Callvirt, dictionaryClear));

                ILProcessor browserIl = browserOpen.Body.GetILProcessor();
                browserIl.InsertBefore(browserOpen.Body.Instructions[0], browserIl.Create(OpCodes.Call, reload));

                assembly.Write(outputPath);
            }
        }

        private static void ValidateOriginalShape(MethodDefinition reload, MethodDefinition browserOpen)
        {
            bool scansDirectory = reload.Body.Instructions.Any(i =>
                i.Operand is MethodReference &&
                ((MethodReference)i.Operand).DeclaringType.FullName == "System.IO.Directory" &&
                ((MethodReference)i.Operand).Name == "EnumerateFiles");
            bool readsFiles = reload.Body.Instructions.Any(i =>
                i.Operand is MethodReference &&
                ((MethodReference)i.Operand).DeclaringType.FullName == "System.IO.File" &&
                ((MethodReference)i.Operand).Name == "ReadAllText");
            bool filtersSolutions = browserOpen.Body.Instructions.Any(i =>
                CallsToken(i, 0x060002A6));

            if (!scansDirectory || !readsFiles || !filtersSolutions)
                throw new InvalidOperationException("Expected solution loading IL shape was not found.");
        }

        private static bool HasCompletePatch(string path)
        {
            var resolver = new DefaultAssemblyResolver();
            resolver.AddSearchDirectory(Path.GetDirectoryName(path));
            var reader = new ReaderParameters { AssemblyResolver = resolver, ReadSymbols = false };

            using (var assembly = AssemblyDefinition.ReadAssembly(path, reader))
            {
                ModuleDefinition module = assembly.MainModule;
                MethodDefinition reload = Method(module, ReloadMethodToken);
                MethodDefinition browserOpen = Method(module, BrowserOpenMethodToken);
                var instructions = reload.Body.Instructions;

                bool clearsBothCaches = instructions.Count >= 6 &&
                    CallsToken(instructions[0], RepositoryAccessorToken) &&
                    LoadsField(instructions[1], SolutionListFieldToken) &&
                    CallsNamed(instructions[2], "Clear") &&
                    CallsToken(instructions[3], RepositoryAccessorToken) &&
                    LoadsField(instructions[4], SolutionPathsFieldToken) &&
                    CallsNamed(instructions[5], "Clear");

                bool reloadsOnBrowserOpen = browserOpen.Body.Instructions.Count > 0 &&
                    CallsToken(browserOpen.Body.Instructions[0], ReloadMethodToken);

                return clearsBothCaches && reloadsOnBrowserOpen;
            }
        }

        private static bool CallsToken(Instruction instruction, uint token)
        {
            var method = instruction.Operand as MethodReference;
            if (method == null || (instruction.OpCode != OpCodes.Call && instruction.OpCode != OpCodes.Callvirt))
                return false;
            return method.MetadataToken.ToUInt32() == token;
        }

        private static bool LoadsField(Instruction instruction, uint token)
        {
            var field = instruction.Operand as FieldReference;
            return instruction.OpCode == OpCodes.Ldfld && field != null &&
                field.MetadataToken.ToUInt32() == token;
        }

        private static bool CallsNamed(Instruction instruction, string name)
        {
            var method = instruction.Operand as MethodReference;
            return method != null && method.Name == name &&
                (instruction.OpCode == OpCodes.Call || instruction.OpCode == OpCodes.Callvirt);
        }

        private static MethodDefinition Method(ModuleDefinition module, uint token)
        {
            var method = module.LookupToken(new MetadataToken(token)) as MethodDefinition;
            if (method == null)
                throw new InvalidOperationException("Method token not found: 0x" + token.ToString("X8"));
            return method;
        }

        private static FieldDefinition Field(ModuleDefinition module, uint token)
        {
            var field = module.LookupToken(new MetadataToken(token)) as FieldDefinition;
            if (field == null)
                throw new InvalidOperationException("Field token not found: 0x" + token.ToString("X8"));
            return field;
        }

        private static string Sha256(string path)
        {
            using (var stream = File.OpenRead(path))
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "");
        }
    }
}
'@

    Add-Type -TypeDefinition $patcherSource -ReferencedAssemblies $cecilDll -Language CSharp
}

switch ($Action) {
    "status" {
        [Shzio.HotReloadPatcher]::Status($resolvedExe)
    }
    "apply" {
        [Shzio.HotReloadPatcher]::Apply($resolvedExe)
    }
    "restore" {
        [Shzio.HotReloadPatcher]::Restore($resolvedExe)
    }
}
