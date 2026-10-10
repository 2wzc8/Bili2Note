# 一键安装 bili2note Skill
# 做两件事：
#   1. 把 bili2note 包全局安装（CLI + Python Tool 可用）
#   2. 把 SKILL.md 复制到 ~/.trae/skills/bili2note/（Trae Agent 自动发现）
#
# 用法：在项目根目录运行 `powershell -File install-skill.ps1`

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "=== Bili2Note 一键安装 ===" -ForegroundColor Cyan
Write-Host ""

# ---- Step 1: 全局安装 pip 包 ----
Write-Host "[1/3] 安装 bili2note Python 包..." -ForegroundColor Yellow

# 优先用 uv tool install（推荐，不污染全局 Python）
$uvOk = Get-Command uv -ErrorAction SilentlyContinue
if ($uvOk) {
    Write-Host "  使用 uv tool install（推荐方式）"
    uv tool install $ProjectRoot --force
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  uv 安装失败，回退到 pip install" -ForegroundColor Yellow
        pip install -e $ProjectRoot
    }
} else {
    Write-Host "  uv 未安装，使用 pip install"
    pip install -e $ProjectRoot
}

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Python 包安装失败，请检查环境" -ForegroundColor Red
    exit 1
}

# 验证
$bili2noteOk = Get-Command bili2note -ErrorAction SilentlyContinue
if ($bili2noteOk) {
    Write-Host "  ✅ bili2note 命令已注册：$($bili2noteOk.Source)" -ForegroundColor Green
} else {
    Write-Host "⚠️ bili2note 命令未找到，请检查 PATH 或手动运行：pip install -e $ProjectRoot" -ForegroundColor Yellow
}

# ---- Step 2: 复制 SKILL.md 到全局技能目录 ----
Write-Host ""
Write-Host "[2/3] 安装 SKILL.md 到全局技能目录..." -ForegroundColor Yellow

$SkillMd = Join-Path $ProjectRoot "skill\SKILL.md"
if (-not (Test-Path $SkillMd)) {
    Write-Host "  ⚠️ SKILL.md 不存在：$SkillMd" -ForegroundColor Yellow
} else {
    # 主目标：~/.agents/skills/（跨 Agent 通用标准，Trae / Claude Code / Codex 等都认）
    $UniversalDir = Join-Path $env:USERPROFILE ".agents\skills\bili2note"
    New-Item -ItemType Directory -Force -Path $UniversalDir | Out-Null
    Copy-Item $SkillMd (Join-Path $UniversalDir "SKILL.md") -Force
    Write-Host "  ✅ SKILL.md → $UniversalDir（跨工具通用）" -ForegroundColor Green

    # 附加目标：~/.trae/skills/（Trae 专属目录，双保险）
    $TraeDir = Join-Path $env:USERPROFILE ".trae\skills\bili2note"
    New-Item -ItemType Directory -Force -Path $TraeDir | Out-Null
    Copy-Item $SkillMd (Join-Path $TraeDir "SKILL.md") -Force
    Write-Host "  ✅ SKILL.md → $TraeDir（Trae 专属，双保险）" -ForegroundColor Green
}

# ---- Step 3: 提醒配置 ----
Write-Host ""
Write-Host "[3/3] 首次使用需要配置（只需做一次）" -ForegroundColor Yellow
Write-Host "  bili2note config llm.provider deepseek          # 选 LLM 提供商"
Write-Host "  bili2note config llm.api_key sk-xxx             # 填 API Key"
Write-Host "  bili2note config bili.cookie 'SESSDATA=xxx'     # 抓字幕需要"
Write-Host ""
Write-Host "可选扩展："
Write-Host "  uv tool install '$ProjectRoot[local]'           # 本地语音识别"
Write-Host "  uv tool install '$ProjectRoot[ocr]'             # 本地 OCR"
Write-Host "  uv tool install '$ProjectRoot[rag]'             # RAG 知识库"

Write-Host ""
Write-Host "✅ 安装完成！重启 Trae 后 Skill 自动生效。" -ForegroundColor Cyan
Write-Host "   试试对 Agent 说：'帮我把 BV1xx411c7mu 做成讲义'"
Write-Host "   或直接 CLI：bili2note note BV1xx411c7mu"
