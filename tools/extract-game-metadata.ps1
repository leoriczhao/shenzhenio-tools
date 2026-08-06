[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$GameExe,

    [Parameter(Mandatory = $true)]
    [string]$Output,

    [string[]]$DetailedTypeNames = @(
        "Index2",
        "Register",
        "Pin",
        "Chip",
        "ChipType",
        "ChipTypes",
        "Terminal",
        "TerminalDirection",
        "TerminalType",
        "PuzzleProvidedChipTerminalPin",
        "Puzzle",
        "Puzzles",
        "Solution",
        "Trace",
        "TraceNet"
    ),

    [string[]]$DisassembleTypeNames = @(
        "ChipTypes",
        "Puzzles"
    ),

    [ValidateRange(0, 8)]
    [int]$StringDecoderDependencyDepth = 8,

    [ValidateRange(1, 512)]
    [int]$StringDecoderDependencyLimit = 128,

    [string]$MonoCecilPath = "",

    [string[]]$ConsumerFieldTokens = @("0x04000A8E"),

    [string[]]$MethodTokens = @(),

    [ValidateRange(0, 8)]
    [int]$MethodDependencyDepth = 0,

    [ValidateRange(1, 512)]
    [int]$MethodDependencyLimit = 128
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$script:DeclaredFlags = [System.Reflection.BindingFlags](
    [System.Reflection.BindingFlags]::Public -bor
    [System.Reflection.BindingFlags]::NonPublic -bor
    [System.Reflection.BindingFlags]::Instance -bor
    [System.Reflection.BindingFlags]::Static -bor
    [System.Reflection.BindingFlags]::DeclaredOnly
)

function Get-AssemblyTypes {
    param([System.Reflection.Assembly]$Assembly)

    try {
        return @($Assembly.GetTypes())
    }
    catch [System.Reflection.ReflectionTypeLoadException] {
        $loadableTypes = @($_.Exception.Types | Where-Object { $null -ne $_ })
        if ($loadableTypes.Count -eq 0) {
            throw
        }
        return $loadableTypes
    }
}

function Get-OpCodeMap {
    $map = @{}
    $flags = [System.Reflection.BindingFlags](
        [System.Reflection.BindingFlags]::Public -bor
        [System.Reflection.BindingFlags]::Static
    )
    foreach ($field in [System.Reflection.Emit.OpCodes].GetFields($flags)) {
        $opcode = [System.Reflection.Emit.OpCode]$field.GetValue($null)
        $key = ([int]$opcode.Value) -band 0xffff
        $map[$key] = $opcode
    }
    return $map
}

function Format-MetadataToken {
    param([int]$Token)
    return "0x{0:X8}" -f ([uint32]$Token)
}

function Get-DisplayText {
    param($Value)
    try {
        return $Value.ToString()
    }
    catch {
        return "<unprintable: $($_.Exception.Message)>"
    }
}

function Get-TypeDisplayName {
    param($TypeValue)
    if ($null -eq $TypeValue) {
        return $null
    }
    $fullNameProperty = $TypeValue.PSObject.Properties["FullName"]
    if ($null -ne $fullNameProperty -and -not [string]::IsNullOrWhiteSpace([string]$fullNameProperty.Value)) {
        return [string]$fullNameProperty.Value
    }
    return Get-DisplayText $TypeValue
}

function Resolve-TokenOperand {
    param(
        [System.Reflection.Module]$Module,
        [int]$Token,
        [string]$TokenKind
    )

    $payload = [ordered]@{
        kind = $TokenKind
        token = Format-MetadataToken $Token
        resolved = $false
        member_kind = $null
        declaring_type = $null
        name = $null
        display = $null
        error = $null
    }

    try {
        $member = $Module.ResolveMember($Token)
        $payload.resolved = $true
        $payload.member_kind = $member.MemberType.ToString()
        if ($null -ne $member.DeclaringType) {
            $payload.declaring_type = $member.DeclaringType.FullName
        }
        $payload.name = $member.Name
        $payload.display = Get-DisplayText $member
    }
    catch {
        $payload.error = $_.Exception.Message
    }

    return $payload
}

function Read-ByteValue {
    param([byte[]]$Bytes, [ref]$Position)
    $value = $Bytes[$Position.Value]
    $Position.Value += 1
    return $value
}

function Read-SByteValue {
    param([byte[]]$Bytes, [ref]$Position)
    $value = [int]$Bytes[$Position.Value]
    if ($value -ge 128) {
        $value -= 256
    }
    $Position.Value += 1
    return $value
}

function Read-UInt16Value {
    param([byte[]]$Bytes, [ref]$Position)
    $value = [System.BitConverter]::ToUInt16($Bytes, $Position.Value)
    $Position.Value += 2
    return $value
}

function Read-Int32Value {
    param([byte[]]$Bytes, [ref]$Position)
    $value = [System.BitConverter]::ToInt32($Bytes, $Position.Value)
    $Position.Value += 4
    return $value
}

function Read-Int64Value {
    param([byte[]]$Bytes, [ref]$Position)
    $value = [System.BitConverter]::ToInt64($Bytes, $Position.Value)
    $Position.Value += 8
    return $value
}

function Read-SingleValue {
    param([byte[]]$Bytes, [ref]$Position)
    $value = [System.BitConverter]::ToSingle($Bytes, $Position.Value)
    $Position.Value += 4
    return $value
}

function Read-DoubleValue {
    param([byte[]]$Bytes, [ref]$Position)
    $value = [System.BitConverter]::ToDouble($Bytes, $Position.Value)
    $Position.Value += 8
    return $value
}

function Read-InstructionOperand {
    param(
        [System.Reflection.Emit.OpCode]$OpCode,
        [byte[]]$Bytes,
        [ref]$Position,
        [System.Reflection.Module]$Module
    )

    $operandType = $OpCode.OperandType.ToString()
    switch ($operandType) {
        "InlineNone" {
            return $null
        }
        "ShortInlineI" {
            return Read-SByteValue $Bytes $Position
        }
        "InlineI" {
            return Read-Int32Value $Bytes $Position
        }
        "InlineI8" {
            return Read-Int64Value $Bytes $Position
        }
        "ShortInlineR" {
            return Read-SingleValue $Bytes $Position
        }
        "InlineR" {
            return Read-DoubleValue $Bytes $Position
        }
        "ShortInlineVar" {
            return [ordered]@{
                kind = "variable"
                index = Read-ByteValue $Bytes $Position
            }
        }
        "InlineVar" {
            return [ordered]@{
                kind = "variable"
                index = Read-UInt16Value $Bytes $Position
            }
        }
        "ShortInlineBrTarget" {
            $delta = Read-SByteValue $Bytes $Position
            return [ordered]@{
                kind = "branch"
                delta = $delta
                target = $Position.Value + $delta
            }
        }
        "InlineBrTarget" {
            $delta = Read-Int32Value $Bytes $Position
            return [ordered]@{
                kind = "branch"
                delta = $delta
                target = $Position.Value + $delta
            }
        }
        "InlineSwitch" {
            $count = Read-Int32Value $Bytes $Position
            $deltas = @()
            for ($index = 0; $index -lt $count; $index++) {
                $deltas += Read-Int32Value $Bytes $Position
            }
            $baseOffset = $Position.Value
            return [ordered]@{
                kind = "switch"
                targets = @($deltas | ForEach-Object { $baseOffset + $_ })
            }
        }
        "InlineString" {
            $token = Read-Int32Value $Bytes $Position
            $payload = [ordered]@{
                kind = "string"
                token = Format-MetadataToken $token
                resolved = $false
                value = $null
                error = $null
            }
            try {
                $payload.value = $Module.ResolveString($token)
                $payload.resolved = $true
            }
            catch {
                $payload.error = $_.Exception.Message
            }
            return $payload
        }
        "InlineSig" {
            $token = Read-Int32Value $Bytes $Position
            $payload = [ordered]@{
                kind = "signature"
                token = Format-MetadataToken $token
                resolved = $false
                bytes_base64 = $null
                error = $null
            }
            try {
                $signature = $Module.ResolveSignature($token)
                $payload.bytes_base64 = [System.Convert]::ToBase64String($signature)
                $payload.resolved = $true
            }
            catch {
                $payload.error = $_.Exception.Message
            }
            return $payload
        }
        "InlineField" {
            $token = Read-Int32Value $Bytes $Position
            return Resolve-TokenOperand $Module $token "field"
        }
        "InlineMethod" {
            $token = Read-Int32Value $Bytes $Position
            return Resolve-TokenOperand $Module $token "method"
        }
        "InlineType" {
            $token = Read-Int32Value $Bytes $Position
            return Resolve-TokenOperand $Module $token "type"
        }
        "InlineTok" {
            $token = Read-Int32Value $Bytes $Position
            return Resolve-TokenOperand $Module $token "token"
        }
        default {
            throw "Unsupported IL operand type: $operandType"
        }
    }
}

function Get-MethodBodyDetails {
    param(
        [System.Reflection.MethodBase]$Method,
        [hashtable]$OpCodeMap
    )

    $body = $Method.GetMethodBody()
    if ($null -eq $body) {
        return $null
    }

    [byte[]]$bytes = $body.GetILAsByteArray()
    $instructions = @()
    $position = 0
    while ($position -lt $bytes.Length) {
        $offset = $position
        $first = $bytes[$position]
        $position += 1
        if ($first -eq 0xfe) {
            if ($position -ge $bytes.Length) {
                throw "Truncated two-byte opcode at IL offset $offset"
            }
            $opcodeKey = 0xfe00 -bor $bytes[$position]
            $position += 1
        }
        else {
            $opcodeKey = [int]$first
        }

        if (-not $OpCodeMap.ContainsKey($opcodeKey)) {
            throw "Unknown opcode 0x$($opcodeKey.ToString('X4')) at IL offset $offset"
        }

        $opcode = [System.Reflection.Emit.OpCode]$OpCodeMap[$opcodeKey]
        $positionRef = [ref]$position
        $operand = Read-InstructionOperand $opcode $bytes $positionRef $Method.Module
        $position = $positionRef.Value
        $instructions += [ordered]@{
            offset = $offset
            offset_hex = "IL_{0:X4}" -f $offset
            opcode = $opcode.Name
            operand_type = $opcode.OperandType.ToString()
            operand = $operand
        }
    }

    return [ordered]@{
        init_locals = $body.InitLocals
        max_stack = $body.MaxStackSize
        local_variables = @(
            $body.LocalVariables | ForEach-Object {
                [ordered]@{
                    index = $_.LocalIndex
                    type = Get-TypeDisplayName $_.LocalType
                    pinned = $_.IsPinned
                }
            }
        )
        exception_clauses = @(
            $body.ExceptionHandlingClauses | ForEach-Object {
                [ordered]@{
                    flags = $_.Flags.ToString()
                    try_offset = $_.TryOffset
                    try_length = $_.TryLength
                    handler_offset = $_.HandlerOffset
                    handler_length = $_.HandlerLength
                    filter_offset = $_.FilterOffset
                    catch_type = Get-TypeDisplayName $_.CatchType
                }
            }
        )
        il_size = $bytes.Length
        instructions = $instructions
    }
}

function Get-ParameterDetails {
    param([System.Reflection.ParameterInfo]$Parameter)
    return [ordered]@{
        position = $Parameter.Position
        name = $Parameter.Name
        type = Get-TypeDisplayName $Parameter.ParameterType
        attributes = $Parameter.Attributes.ToString()
        optional = $Parameter.IsOptional
    }
}

function Get-MethodDetails {
    param([System.Reflection.MethodBase]$Method)

    $inspectionErrors = @()
    $body = $null
    try {
        $body = $Method.GetMethodBody()
    }
    catch {
        $body = $null
        $inspectionErrors += "body: $($_.Exception.Message)"
    }

    $returnType = $null
    if ($Method -is [System.Reflection.MethodInfo]) {
        try {
            $returnType = Get-TypeDisplayName ([System.Reflection.MethodInfo]$Method).ReturnType
        }
        catch {
            $inspectionErrors += "return type: $($_.Exception.Message)"
        }
    }

    $genericArguments = @()
    if ($Method -is [System.Reflection.MethodInfo]) {
        try {
            $genericArguments = @($Method.GetGenericArguments() | ForEach-Object { Get-TypeDisplayName $_ })
        }
        catch {
            $genericArguments = @()
            $inspectionErrors += "generic arguments: $($_.Exception.Message)"
        }
    }

    $parameters = @()
    try {
        $parameters = @($Method.GetParameters() | ForEach-Object { Get-ParameterDetails $_ })
    }
    catch {
        $inspectionErrors += "parameters: $($_.Exception.Message)"
    }

    $callingConvention = $null
    try {
        $callingConvention = $Method.CallingConvention.ToString()
    }
    catch {
        $inspectionErrors += "calling convention: $($_.Exception.Message)"
    }

    $ilSize = 0
    if ($null -ne $body) {
        $ilBytes = $body.GetILAsByteArray()
        if ($null -ne $ilBytes) {
            $ilSize = $ilBytes.Length
        }
    }

    return [ordered]@{
        name = $Method.Name
        metadata_token = Format-MetadataToken $Method.MetadataToken
        attributes = $Method.Attributes.ToString()
        calling_convention = $callingConvention
        return_type = $returnType
        parameters = $parameters
        generic_arguments = $genericArguments
        has_body = $null -ne $body
        il_size = $ilSize
        inspection_errors = $inspectionErrors
    }
}

function Get-FieldDetails {
    param([System.Reflection.FieldInfo]$Field)

    $constant = $null
    if ($Field.IsLiteral) {
        try {
            $rawConstant = $Field.GetRawConstantValue()
            if ($null -ne $rawConstant) {
                if ($rawConstant -is [string] -or $rawConstant.GetType().IsPrimitive) {
                    $constant = $rawConstant
                }
                else {
                    $constant = Get-DisplayText $rawConstant
                }
            }
        }
        catch {
            $constant = $null
        }
    }

    return [ordered]@{
        name = $Field.Name
        metadata_token = Format-MetadataToken $Field.MetadataToken
        type = Get-TypeDisplayName $Field.FieldType
        attributes = $Field.Attributes.ToString()
        static = $Field.IsStatic
        init_only = $Field.IsInitOnly
        literal = $Field.IsLiteral
        constant = $constant
    }
}

function Get-TypeDetails {
    param([System.Type]$Type)

    $constructors = @($Type.GetConstructors($script:DeclaredFlags))
    if ($null -ne $Type.TypeInitializer) {
        $initializerToken = $Type.TypeInitializer.MetadataToken
        if (-not ($constructors | Where-Object { $_.MetadataToken -eq $initializerToken })) {
            $constructors += $Type.TypeInitializer
        }
    }

    $constructorDetails = @(
        $constructors | Sort-Object MetadataToken | ForEach-Object {
            $constructor = $_
            try {
                Get-MethodDetails $constructor
            }
            catch {
                throw "Failed to inspect constructor $($Type.FullName).$($constructor.Name): $($_.Exception.Message)"
            }
        }
    )
    $methodDetails = @(
        $Type.GetMethods($script:DeclaredFlags) | Sort-Object MetadataToken | ForEach-Object {
            $method = $_
            try {
                Get-MethodDetails $method
            }
            catch {
                throw "Failed to inspect method $($Type.FullName).$($method.Name): $($_.Exception.Message)"
            }
        }
    )

    return [ordered]@{
        name = $Type.Name
        full_name = $Type.FullName
        metadata_token = Format-MetadataToken $Type.MetadataToken
        attributes = $Type.Attributes.ToString()
        base_type = Get-TypeDisplayName $Type.BaseType
        is_enum = $Type.IsEnum
        is_value_type = $Type.IsValueType
        interfaces = @($Type.GetInterfaces() | ForEach-Object { Get-TypeDisplayName $_ } | Sort-Object)
        nested_types = @($Type.GetNestedTypes($script:DeclaredFlags) | ForEach-Object { Get-TypeDisplayName $_ } | Sort-Object)
        fields = @($Type.GetFields($script:DeclaredFlags) | Sort-Object MetadataToken | ForEach-Object { Get-FieldDetails $_ })
        constructors = $constructorDetails
        methods = $methodDetails
    }
}

function Find-TypeByName {
    param(
        [System.Type[]]$Types,
        [string]$Name
    )
    return $Types | Where-Object { $_.FullName -eq $Name -or $_.Name -eq $Name } | Select-Object -First 1
}

function Find-InitializationMethods {
    param([System.Type]$Type)

    if ($null -ne $Type.TypeInitializer) {
        return @($Type.TypeInitializer)
    }

    $candidates = @()
    foreach ($method in $Type.GetMethods($script:DeclaredFlags)) {
        if (-not $method.IsStatic) {
            continue
        }
        if ((Get-TypeDisplayName $method.ReturnType) -ne "System.Void") {
            continue
        }
        try {
            if ($method.GetParameters().Count -ne 0) {
                continue
            }
            $body = $method.GetMethodBody()
            if ($null -eq $body -or $null -eq $body.GetILAsByteArray()) {
                continue
            }
            $candidates += $method
        }
        catch {
            continue
        }
    }

    return @(
        $candidates |
            Sort-Object @{ Expression = { $_.GetMethodBody().GetILAsByteArray().Length }; Descending = $true } |
            Select-Object -First 1
    )
}

function Get-ManifestResourceDetails {
    param([System.Reflection.Assembly]$Assembly)

    $resources = @()
    foreach ($resourceName in $Assembly.GetManifestResourceNames()) {
        $stream = $null
        try {
            $stream = $Assembly.GetManifestResourceStream($resourceName)
            if ($null -eq $stream) {
                throw "resource stream is null"
            }
            $memory = New-Object System.IO.MemoryStream
            try {
                $stream.CopyTo($memory)
                $bytes = $memory.ToArray()
            }
            finally {
                $memory.Dispose()
            }

            $sha = [System.Security.Cryptography.SHA256]::Create()
            try {
                $digest = $sha.ComputeHash($bytes)
            }
            finally {
                $sha.Dispose()
            }

            $resources += [ordered]@{
                name = $resourceName
                size = $bytes.Length
                sha256 = ([System.BitConverter]::ToString($digest) -replace "-", "").ToLowerInvariant()
                data_base64 = [System.Convert]::ToBase64String($bytes)
                error = $null
            }
        }
        catch {
            $resources += [ordered]@{
                name = $resourceName
                size = $null
                sha256 = $null
                data_base64 = $null
                error = $_.Exception.Message
            }
        }
        finally {
            if ($null -ne $stream) {
                $stream.Dispose()
            }
        }
    }
    return $resources
}

function Find-InitializedDataFieldTokens {
    param([object[]]$Disassembly)

    $tokens = @{}
    foreach ($entry in $Disassembly) {
        if ($null -eq $entry.body) {
            continue
        }
        $instructions = @($entry.body.instructions)
        for ($index = 0; $index -lt $instructions.Count - 1; $index++) {
            $instruction = $instructions[$index]
            $next = $instructions[$index + 1]
            if ($instruction.opcode -ne "ldtoken" -or $next.opcode -ne "call") {
                continue
            }
            if ($null -eq $instruction.operand -or $null -eq $next.operand) {
                continue
            }
            if ($next.operand.name -ne "InitializeArray") {
                continue
            }
            $tokenText = [string]$instruction.operand.token
            if ($tokenText -match "^0x04[0-9A-Fa-f]{6}$") {
                $tokens[$tokenText] = $true
            }
        }
    }
    return @($tokens.Keys | Sort-Object)
}

function Get-InitializedDataDetails {
    param(
        [string]$AssemblyPath,
        [string[]]$FieldTokens,
        [string]$CecilPath
    )

    if ($FieldTokens.Count -eq 0) {
        return @()
    }
    if (-not (Test-Path -LiteralPath $CecilPath -PathType Leaf)) {
        throw "Mono.Cecil not found: $CecilPath"
    }

    [System.Reflection.Assembly]::LoadFrom($CecilPath) | Out-Null
    $cecilAssembly = [Mono.Cecil.AssemblyDefinition]::ReadAssembly($AssemblyPath)
    try {
        $details = @()
        foreach ($tokenText in $FieldTokens) {
            $token = [System.Convert]::ToInt32($tokenText.Substring(2), 16)
            try {
                $field = $cecilAssembly.MainModule.LookupToken($token)
                $bytes = [byte[]]$field.InitialValue
                $sha = [System.Security.Cryptography.SHA256]::Create()
                try {
                    $digest = $sha.ComputeHash($bytes)
                }
                finally {
                    $sha.Dispose()
                }
                $details += [ordered]@{
                    metadata_token = $tokenText
                    name = $field.Name
                    field_type = $field.FieldType.FullName
                    rva = $field.RVA
                    size = $bytes.Length
                    sha256 = ([System.BitConverter]::ToString($digest) -replace "-", "").ToLowerInvariant()
                    data_base64 = [System.Convert]::ToBase64String($bytes)
                    error = $null
                }
            }
            catch {
                $details += [ordered]@{
                    metadata_token = $tokenText
                    name = $null
                    field_type = $null
                    rva = $null
                    size = $null
                    sha256 = $null
                    data_base64 = $null
                    error = $_.Exception.Message
                }
            }
        }
        return $details
    }
    finally {
        $cecilAssembly.Dispose()
    }
}

function Find-FieldConsumerMethods {
    param(
        [string]$AssemblyPath,
        [string[]]$FieldTokens,
        [string]$CecilPath
    )

    if ($FieldTokens.Count -eq 0) {
        return @()
    }
    if (-not (Test-Path -LiteralPath $CecilPath -PathType Leaf)) {
        throw "Mono.Cecil not found: $CecilPath"
    }

    [System.Reflection.Assembly]::LoadFrom($CecilPath) | Out-Null
    $targetTokens = @{}
    foreach ($tokenText in $FieldTokens) {
        $targetTokens[[System.Convert]::ToInt32($tokenText.Substring(2), 16)] = $tokenText
    }

    $cecilAssembly = [Mono.Cecil.AssemblyDefinition]::ReadAssembly($AssemblyPath)
    try {
        $queue = New-Object System.Collections.Queue
        foreach ($type in $cecilAssembly.MainModule.Types) {
            $queue.Enqueue($type)
        }
        $consumers = @{}
        while ($queue.Count -gt 0) {
            $type = $queue.Dequeue()
            foreach ($nestedType in $type.NestedTypes) {
                $queue.Enqueue($nestedType)
            }
            foreach ($method in $type.Methods) {
                if (-not $method.HasBody) {
                    continue
                }
                foreach ($instruction in $method.Body.Instructions) {
                    $operand = $instruction.Operand
                    if ($null -eq $operand -or $operand -isnot [Mono.Cecil.FieldReference]) {
                        continue
                    }
                    $fieldToken = $operand.MetadataToken.ToInt32()
                    if (-not $targetTokens.ContainsKey($fieldToken)) {
                        continue
                    }
                    $methodToken = $method.MetadataToken.ToInt32()
                    if (-not $consumers.ContainsKey($methodToken)) {
                        $consumers[$methodToken] = [ordered]@{
                            method_token = Format-MetadataToken $methodToken
                            field_tokens = @()
                        }
                    }
                    if ($consumers[$methodToken].field_tokens -notcontains $targetTokens[$fieldToken]) {
                        $consumers[$methodToken].field_tokens += $targetTokens[$fieldToken]
                    }
                }
            }
        }
        return @($consumers.Values | Sort-Object method_token)
    }
    finally {
        $cecilAssembly.Dispose()
    }
}

function Find-StringDecoderMethods {
    param(
        [object[]]$Disassembly,
        [System.Reflection.Module]$Module
    )

    $tokens = @{}
    foreach ($entry in $Disassembly) {
        if ($null -eq $entry.body) {
            continue
        }
        foreach ($instruction in $entry.body.instructions) {
            if ($instruction.opcode -ne "call" -and $instruction.opcode -ne "callvirt") {
                continue
            }
            $operand = $instruction.operand
            if ($null -eq $operand -or $operand.kind -ne "method" -or -not $operand.resolved) {
                continue
            }
            if ($operand.token -notmatch "^0x06[0-9A-Fa-f]{6}$") {
                continue
            }
            $token = [System.Convert]::ToInt32($operand.token.Substring(2), 16)
            try {
                $method = $Module.ResolveMethod($token)
                if (-not $method.IsStatic) {
                    continue
                }
                if ((Get-TypeDisplayName $method.ReturnType) -ne "System.String") {
                    continue
                }
                $parameters = @($method.GetParameters())
                if ($parameters.Count -ne 1 -or (Get-TypeDisplayName $parameters[0].ParameterType) -ne "System.Int32") {
                    continue
                }
                $tokens[$token] = $method
            }
            catch {
                continue
            }
        }
    }
    return @($tokens.Values | Sort-Object MetadataToken)
}

function Get-ModuleMethodDependencies {
    param(
        [System.Reflection.MethodBase[]]$Roots,
        [System.Reflection.Module]$Module,
        [hashtable]$OpCodeMap,
        [int]$MaxDepth,
        [int]$Limit
    )

    $rootTokens = @{}
    $seenTokens = @{}
    $queue = New-Object System.Collections.Queue
    foreach ($root in $Roots) {
        if ($null -eq $root) {
            continue
        }
        $rootTokens[$root.MetadataToken] = $true
        $seenTokens[$root.MetadataToken] = $true
        $queue.Enqueue([pscustomobject]@{
            method = $root
            depth = 0
        })
    }

    $dependencies = @()
    while ($queue.Count -gt 0) {
        $current = $queue.Dequeue()
        if ($current.depth -ge $MaxDepth) {
            continue
        }

        try {
            $body = Get-MethodBodyDetails $current.method $OpCodeMap
        }
        catch {
            continue
        }
        if ($null -eq $body) {
            continue
        }

        foreach ($instruction in $body.instructions) {
            $operand = $instruction.operand
            if ($null -eq $operand -or $operand -isnot [System.Collections.IDictionary]) {
                continue
            }
            if (
                -not $operand.Contains("kind") -or
                -not $operand.Contains("resolved") -or
                $operand["kind"] -ne "method" -or
                -not $operand["resolved"]
            ) {
                continue
            }
            if (-not $operand.Contains("token") -or $operand["token"] -notmatch "^0x06[0-9A-Fa-f]{6}$") {
                continue
            }

            $tokenText = [string]$operand["token"]
            $token = [System.Convert]::ToInt32($tokenText.Substring(2), 16)
            if ($seenTokens.ContainsKey($token)) {
                continue
            }

            try {
                $method = $Module.ResolveMethod($token)
                if ($method.Module.ModuleVersionId -ne $Module.ModuleVersionId) {
                    continue
                }
                if ($null -eq $method.GetMethodBody()) {
                    continue
                }
            }
            catch {
                continue
            }

            $seenTokens[$token] = $true
            $dependencyDepth = $current.depth + 1
            $dependencies += [pscustomobject]@{
                method = $method
                depth = $dependencyDepth
                referenced_by = Format-MetadataToken $current.method.MetadataToken
            }
            if ($dependencies.Count -ge $Limit) {
                return $dependencies
            }
            $queue.Enqueue([pscustomobject]@{
                method = $method
                depth = $dependencyDepth
            })

            $typeInitializer = $method.DeclaringType.TypeInitializer
            if (
                $null -ne $typeInitializer -and
                -not $seenTokens.ContainsKey($typeInitializer.MetadataToken)
            ) {
                $seenTokens[$typeInitializer.MetadataToken] = $true
                $dependencies += [pscustomobject]@{
                    method = $typeInitializer
                    depth = $dependencyDepth
                    referenced_by = Format-MetadataToken $method.MetadataToken
                }
                if ($dependencies.Count -ge $Limit) {
                    return $dependencies
                }
                $queue.Enqueue([pscustomobject]@{
                    method = $typeInitializer
                    depth = $dependencyDepth
                })
            }
        }
    }

    return $dependencies
}

function Get-OwnedMethodDependencies {
    param(
        [System.Reflection.MethodBase[]]$Roots,
        [System.Reflection.Module]$Module,
        [hashtable]$OpCodeMap,
        [int]$MaxDepth
    )

    $ownerNames = @($Roots | ForEach-Object { $_.DeclaringType.FullName } | Sort-Object -Unique)
    $seenTokens = @{}
    $queue = New-Object System.Collections.Queue
    foreach ($root in $Roots) {
        $seenTokens[$root.MetadataToken] = $true
        $queue.Enqueue([pscustomobject]@{ method = $root; depth = 0 })
    }

    $dependencies = @()
    while ($queue.Count -gt 0) {
        $current = $queue.Dequeue()
        if ($current.depth -ge $MaxDepth) {
            continue
        }
        try {
            $body = Get-MethodBodyDetails $current.method $OpCodeMap
        }
        catch {
            continue
        }
        if ($null -eq $body) {
            continue
        }

        foreach ($instruction in $body.instructions) {
            $operand = $instruction.operand
            if ($null -eq $operand -or $operand -isnot [System.Collections.IDictionary]) {
                continue
            }
            if (
                -not $operand.Contains("kind") -or
                -not $operand.Contains("resolved") -or
                $operand["kind"] -ne "method" -or
                -not $operand["resolved"] -or
                -not $operand.Contains("token") -or
                $operand["token"] -notmatch "^0x06[0-9A-Fa-f]{6}$"
            ) {
                continue
            }

            $tokenText = [string]$operand["token"]
            $token = [System.Convert]::ToInt32($tokenText.Substring(2), 16)
            if ($seenTokens.ContainsKey($token)) {
                continue
            }
            try {
                $method = $Module.ResolveMethod($token)
                if ($method.Module.ModuleVersionId -ne $Module.ModuleVersionId) {
                    continue
                }
                if ($null -eq $method.GetMethodBody()) {
                    continue
                }
            }
            catch {
                continue
            }

            $declaringName = $method.DeclaringType.FullName
            $owned = $false
            foreach ($ownerName in $ownerNames) {
                if ($declaringName -eq $ownerName -or $declaringName.StartsWith("$ownerName+")) {
                    $owned = $true
                    break
                }
            }
            if (-not $owned) {
                continue
            }

            $seenTokens[$token] = $true
            $depth = $current.depth + 1
            $dependencies += [pscustomobject]@{
                method = $method
                depth = $depth
                referenced_by = Format-MetadataToken $current.method.MetadataToken
            }
            $queue.Enqueue([pscustomobject]@{ method = $method; depth = $depth })
        }
    }
    return $dependencies
}

$resolvedExe = [System.IO.Path]::GetFullPath($GameExe)
if (-not (Test-Path -LiteralPath $resolvedExe -PathType Leaf)) {
    throw "Game executable not found: $resolvedExe"
}

$resolvedOutput = [System.IO.Path]::GetFullPath($Output)
$outputDirectory = [System.IO.Path]::GetDirectoryName($resolvedOutput)
if (-not [string]::IsNullOrWhiteSpace($outputDirectory)) {
    [System.IO.Directory]::CreateDirectory($outputDirectory) | Out-Null
}

$hash = (Get-FileHash -LiteralPath $resolvedExe -Algorithm SHA256).Hash.ToLowerInvariant()
$file = Get-Item -LiteralPath $resolvedExe
$fileVersion = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($resolvedExe)
$assembly = [System.Reflection.Assembly]::ReflectionOnlyLoadFrom($resolvedExe)
$module = $assembly.ManifestModule
$allTypes = @(Get-AssemblyTypes $assembly)
$opCodeMap = Get-OpCodeMap

$detailedTypes = @()
$detailedTypeObjects = @()
$missingDetailedTypes = @()
foreach ($typeName in $DetailedTypeNames) {
    $type = Find-TypeByName $allTypes $typeName
    if ($null -eq $type) {
        $missingDetailedTypes += $typeName
        continue
    }
    $detailedTypes += Get-TypeDetails $type
    $detailedTypeObjects += $type
}

# Include game-owned types directly referenced by core fields. This discovers
# obfuscated record types such as the provided-chip descriptor without relying
# on unstable names and does not read any static field values.
$referencedTypeObjects = @()
$seenDetailedTypeTokens = @{}
foreach ($type in $detailedTypeObjects) {
    $seenDetailedTypeTokens[$type.MetadataToken] = $true
}
foreach ($rootType in @($detailedTypeObjects)) {
    foreach ($field in $rootType.GetFields($script:DeclaredFlags)) {
        $fieldType = $field.FieldType
        if ($null -eq $fieldType) {
            continue
        }
        $candidates = @($fieldType)
        $fieldElementType = $fieldType.GetElementType()
        if ($null -ne $fieldElementType) {
            $candidates += $fieldElementType
        }
        $candidates += @($fieldType.GetGenericArguments())
        foreach ($candidateValue in @($candidates)) {
            if ($null -eq $candidateValue) {
                continue
            }
            $candidate = $candidateValue
            $candidateElementType = $candidate.GetElementType()
            if ($null -ne $candidateElementType) {
                $candidate = $candidateElementType
            }
            if ($null -eq $candidate -or $candidate.Module.ModuleVersionId -ne $module.ModuleVersionId) {
                continue
            }
            if ($seenDetailedTypeTokens.ContainsKey($candidate.MetadataToken)) {
                continue
            }
            $seenDetailedTypeTokens[$candidate.MetadataToken] = $true
            $referencedTypeObjects += $candidate
            $detailedTypes += Get-TypeDetails $candidate
        }
    }
}

$disassembly = @()
$initializerMethods = @()
$missingDisassemblyTypes = @()
foreach ($typeName in $DisassembleTypeNames) {
    $type = Find-TypeByName $allTypes $typeName
    if ($null -eq $type) {
        $missingDisassemblyTypes += $typeName
        continue
    }
    $initializers = @(Find-InitializationMethods $type)
    if ($initializers.Count -eq 0) {
        $disassembly += [ordered]@{
            type = $type.FullName
            method = $null
            metadata_token = $null
            body = $null
            error = "type has no discoverable static initialization method"
        }
        continue
    }

    foreach ($initializer in $initializers) {
        $initializerMethods += $initializer
        try {
            $bodyDetails = Get-MethodBodyDetails $initializer $opCodeMap
            $disassembly += [ordered]@{
                category = "initializer"
                type = $type.FullName
                method = $initializer.Name
                metadata_token = Format-MetadataToken $initializer.MetadataToken
                body = $bodyDetails
                error = $null
            }
        }
        catch {
            $disassembly += [ordered]@{
                category = "initializer"
                type = $type.FullName
                method = $initializer.Name
                metadata_token = Format-MetadataToken $initializer.MetadataToken
                body = $null
                error = $_.Exception.Message
            }
        }
    }
}

$initializerDependencies = @(Get-OwnedMethodDependencies $initializerMethods $module $opCodeMap 4)
foreach ($dependency in $initializerDependencies) {
    $method = $dependency.method
    try {
        $disassembly += [ordered]@{
            category = "initializer_dependency"
            dependency_depth = $dependency.depth
            referenced_by = $dependency.referenced_by
            type = $method.DeclaringType.FullName
            method = $method.Name
            metadata_token = Format-MetadataToken $method.MetadataToken
            body = Get-MethodBodyDetails $method $opCodeMap
            error = $null
        }
    }
    catch {
        $disassembly += [ordered]@{
            category = "initializer_dependency"
            dependency_depth = $dependency.depth
            referenced_by = $dependency.referenced_by
            type = $method.DeclaringType.FullName
            method = $method.Name
            metadata_token = Format-MetadataToken $method.MetadataToken
            body = $null
            error = $_.Exception.Message
        }
    }
    $dependencyTypeName = $method.DeclaringType.FullName
    if (-not ($detailedTypes | Where-Object { $_.full_name -eq $dependencyTypeName })) {
        $detailedTypes += Get-TypeDetails $method.DeclaringType
    }
}

$stringDecoderMethods = @(Find-StringDecoderMethods $disassembly $module)
$stringDecoderRoots = @()
foreach ($decoder in $stringDecoderMethods) {
    try {
        $bodyDetails = Get-MethodBodyDetails $decoder $opCodeMap
        $disassembly += [ordered]@{
            category = "string_decoder_candidate"
            type = $decoder.DeclaringType.FullName
            method = $decoder.Name
            metadata_token = Format-MetadataToken $decoder.MetadataToken
            body = $bodyDetails
            error = $null
        }
    }
    catch {
        $disassembly += [ordered]@{
            category = "string_decoder_candidate"
            type = $decoder.DeclaringType.FullName
            method = $decoder.Name
            metadata_token = Format-MetadataToken $decoder.MetadataToken
            body = $null
            error = $_.Exception.Message
        }
    }

    $decoderTypeName = $decoder.DeclaringType.FullName
    if (-not ($detailedTypes | Where-Object { $_.full_name -eq $decoderTypeName })) {
        $detailedTypes += Get-TypeDetails $decoder.DeclaringType
    }

    foreach ($method in $decoder.DeclaringType.GetMethods($script:DeclaredFlags)) {
        try {
            if ($method.IsStatic -and $null -ne $method.GetMethodBody()) {
                $stringDecoderRoots += $method
            }
        }
        catch {
            continue
        }
    }
    if ($null -ne $decoder.DeclaringType.TypeInitializer) {
        $stringDecoderRoots += $decoder.DeclaringType.TypeInitializer
    }
}

$stringDecoderRoots = @(
    $stringDecoderRoots |
        Group-Object MetadataToken |
        ForEach-Object { $_.Group | Select-Object -First 1 } |
        Sort-Object MetadataToken
)

foreach ($root in $stringDecoderRoots) {
    if ($stringDecoderMethods | Where-Object { $_.MetadataToken -eq $root.MetadataToken }) {
        continue
    }
    try {
        $disassembly += [ordered]@{
            category = "string_decoder_companion"
            dependency_depth = 0
            referenced_by = $null
            type = $root.DeclaringType.FullName
            method = $root.Name
            metadata_token = Format-MetadataToken $root.MetadataToken
            body = Get-MethodBodyDetails $root $opCodeMap
            error = $null
        }
    }
    catch {
        $disassembly += [ordered]@{
            category = "string_decoder_companion"
            dependency_depth = 0
            referenced_by = $null
            type = $root.DeclaringType.FullName
            method = $root.Name
            metadata_token = Format-MetadataToken $root.MetadataToken
            body = $null
            error = $_.Exception.Message
        }
    }
}

$stringDecoderDependencies = @(
    Get-ModuleMethodDependencies `
        $stringDecoderRoots `
        $module `
        $opCodeMap `
        $StringDecoderDependencyDepth `
        $StringDecoderDependencyLimit
)
foreach ($dependency in $stringDecoderDependencies) {
    $method = $dependency.method
    try {
        $disassembly += [ordered]@{
            category = "string_decoder_dependency"
            dependency_depth = $dependency.depth
            referenced_by = $dependency.referenced_by
            type = $method.DeclaringType.FullName
            method = $method.Name
            metadata_token = Format-MetadataToken $method.MetadataToken
            body = Get-MethodBodyDetails $method $opCodeMap
            error = $null
        }
    }
    catch {
        $disassembly += [ordered]@{
            category = "string_decoder_dependency"
            dependency_depth = $dependency.depth
            referenced_by = $dependency.referenced_by
            type = $method.DeclaringType.FullName
            method = $method.Name
            metadata_token = Format-MetadataToken $method.MetadataToken
            body = $null
            error = $_.Exception.Message
        }
    }

    $dependencyTypeName = $method.DeclaringType.FullName
    if (-not ($detailedTypes | Where-Object { $_.full_name -eq $dependencyTypeName })) {
        $detailedTypes += Get-TypeDetails $method.DeclaringType
    }
}

$manifestResources = @(Get-ManifestResourceDetails $assembly)

$initializedDataFieldTokens = @(Find-InitializedDataFieldTokens $disassembly)
$initializedDataFields = @()
$initializedDataError = $null
if ([string]::IsNullOrWhiteSpace($MonoCecilPath)) {
    $MonoCecilPath = Join-Path $PSScriptRoot ".deps\mono.cecil\0.11.6\lib\net40\Mono.Cecil.dll"
}
try {
    $initializedDataFields = @(
        Get-InitializedDataDetails $resolvedExe $initializedDataFieldTokens $MonoCecilPath
    )
}
catch {
    $initializedDataError = $_.Exception.Message
}

$fieldConsumerMethods = @()
$fieldConsumerError = $null
try {
    $fieldConsumerMethods = @(
        Find-FieldConsumerMethods $resolvedExe $ConsumerFieldTokens $MonoCecilPath
    )
    foreach ($consumer in $fieldConsumerMethods) {
        $token = [System.Convert]::ToInt32($consumer.method_token.Substring(2), 16)
        $method = $module.ResolveMethod($token)
        if ($initializerMethods | Where-Object { $_.MetadataToken -eq $method.MetadataToken }) {
            continue
        }
        $consumerBody = $null
        $consumerError = $null
        try {
            $consumerBody = Get-MethodBodyDetails $method $opCodeMap
        }
        catch {
            $consumerError = $_.Exception.Message
        }
        $disassembly += [ordered]@{
            category = "field_consumer"
            consumed_field_tokens = $consumer.field_tokens
            type = $method.DeclaringType.FullName
            method = $method.Name
            metadata_token = Format-MetadataToken $method.MetadataToken
            body = $consumerBody
            error = $consumerError
        }
        $consumerTypeName = $method.DeclaringType.FullName
        if (-not ($detailedTypes | Where-Object { $_.full_name -eq $consumerTypeName })) {
            $detailedTypes += Get-TypeDetails $method.DeclaringType
        }
    }
}
catch {
    $fieldConsumerError = $_.Exception.Message
}

$targetMethods = @()
$targetMethodErrors = @()
foreach ($tokenText in $MethodTokens) {
    try {
        if ($tokenText -notmatch "^0x06[0-9A-Fa-f]{6}$") {
            throw "method token must have the form 0x06NNNNNN"
        }
        $token = [System.Convert]::ToInt32($tokenText.Substring(2), 16)
        $method = $module.ResolveMethod($token)
        if ($method.Module.ModuleVersionId -ne $module.ModuleVersionId) {
            throw "method belongs to another module"
        }
        if ($null -eq $method.GetMethodBody()) {
            throw "method has no IL body"
        }
        $targetMethods += $method
        if (-not ($disassembly | Where-Object { $_.metadata_token -eq $tokenText })) {
            $disassembly += [ordered]@{
                category = "target_method"
                type = $method.DeclaringType.FullName
                method = $method.Name
                metadata_token = Format-MetadataToken $method.MetadataToken
                body = Get-MethodBodyDetails $method $opCodeMap
                error = $null
            }
        }
        $targetTypeName = $method.DeclaringType.FullName
        if (-not ($detailedTypes | Where-Object { $_.full_name -eq $targetTypeName })) {
            try {
                $detailedTypes += Get-TypeDetails $method.DeclaringType
            }
            catch {
                $targetMethodErrors += [ordered]@{
                    metadata_token = Format-MetadataToken $method.MetadataToken
                    error = "type details: $($_.Exception.Message)"
                }
            }
        }
    }
    catch {
        $targetMethodErrors += [ordered]@{
            metadata_token = $tokenText
            error = $_.Exception.Message
        }
    }
}

$targetMethodDependencies = @()
if ($targetMethods.Count -gt 0 -and $MethodDependencyDepth -gt 0) {
    $targetMethodDependencies = @(
        Get-ModuleMethodDependencies `
            $targetMethods `
            $module `
            $opCodeMap `
            $MethodDependencyDepth `
            $MethodDependencyLimit
    )
    foreach ($dependency in $targetMethodDependencies) {
        $method = $dependency.method
        $methodToken = Format-MetadataToken $method.MetadataToken
        if (-not ($disassembly | Where-Object { $_.metadata_token -eq $methodToken })) {
            $disassembly += [ordered]@{
                category = "target_method_dependency"
                dependency_depth = $dependency.depth
                referenced_by = $dependency.referenced_by
                type = $method.DeclaringType.FullName
                method = $method.Name
                metadata_token = $methodToken
                body = Get-MethodBodyDetails $method $opCodeMap
                error = $null
            }
        }
        $dependencyTypeName = $method.DeclaringType.FullName
        if (-not ($detailedTypes | Where-Object { $_.full_name -eq $dependencyTypeName })) {
            try {
                $detailedTypes += Get-TypeDetails $method.DeclaringType
            }
            catch {
                $targetMethodErrors += [ordered]@{
                    metadata_token = Format-MetadataToken $method.MetadataToken
                    error = "dependency type details: $($_.Exception.Message)"
                }
            }
        }
    }
}

$chipTypesType = Find-TypeByName $allTypes "ChipTypes"
$chipTypeFieldCount = 0
if ($null -ne $chipTypesType) {
    $chipTypeFieldCount = @(
        $chipTypesType.GetFields($script:DeclaredFlags) |
            Where-Object { $_.IsStatic -and $_.FieldType.Name -eq "ChipType" }
    ).Count
}

$payload = [ordered]@{
    format = "shzio-game-metadata"
    format_version = 1
    extractor = [ordered]@{
        name = "extract-game-metadata.ps1"
        version = 2
        generated_at_utc = [System.DateTimeOffset]::UtcNow.ToString("o")
        reflection_only = $true
        static_constructors_executed = $false
    }
    source = [ordered]@{
        path = $resolvedExe
        size = $file.Length
        last_write_utc = $file.LastWriteTimeUtc.ToString("o")
        sha256 = $hash
        file_version = $fileVersion.FileVersion
        assembly_full_name = $assembly.FullName
        module_name = $module.Name
        module_version_id = $module.ModuleVersionId.ToString()
    }
    summary = [ordered]@{
        type_count = $allTypes.Count
        detailed_type_count = $detailedTypes.Count
        referenced_detailed_type_count = $referencedTypeObjects.Count
        missing_detailed_types = $missingDetailedTypes
        chip_type_static_field_count = $chipTypeFieldCount
        disassembled_method_count = @($disassembly | Where-Object { $null -ne $_.body }).Count
        initializer_dependency_count = $initializerDependencies.Count
        string_decoder_candidate_count = $stringDecoderMethods.Count
        string_decoder_companion_count = $stringDecoderRoots.Count - $stringDecoderMethods.Count
        string_decoder_dependency_count = $stringDecoderDependencies.Count
        string_decoder_dependency_depth = $StringDecoderDependencyDepth
        string_decoder_dependency_limit = $StringDecoderDependencyLimit
        manifest_resource_count = $manifestResources.Count
        initialized_data_reference_count = $initializedDataFieldTokens.Count
        initialized_data_field_count = @($initializedDataFields | Where-Object { $null -eq $_.error }).Count
        initialized_data_error = $initializedDataError
        field_consumer_count = @($disassembly | Where-Object { $_.category -eq "field_consumer" }).Count
        field_consumer_error = $fieldConsumerError
        target_method_count = $targetMethods.Count
        target_method_dependency_count = $targetMethodDependencies.Count
        target_method_dependency_depth = $MethodDependencyDepth
        target_method_dependency_limit = $MethodDependencyLimit
        target_method_errors = $targetMethodErrors
        missing_disassembly_types = $missingDisassemblyTypes
    }
    type_names = @($allTypes | ForEach-Object { $_.FullName } | Sort-Object)
    types = $detailedTypes
    disassembly = $disassembly
    manifest_resources = $manifestResources
    initialized_data_fields = $initializedDataFields
}

$json = $payload | ConvertTo-Json -Depth 100
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllText($resolvedOutput, $json, $utf8NoBom)

Write-Output $resolvedOutput
