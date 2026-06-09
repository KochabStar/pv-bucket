# pv-bucket

> pv 包管理器的官方 bucket，收录常用开发工具和桌面软件。

## 使用

```bash
# 添加 bucket（默认已添加）
pv bucket add main https://github.com/loonghao/pv-bucket

# 同步最新清单
pv sync

# 搜索安装
pv search ripgrep
pv install ripgrep
```

## 目录结构

```
├── util/         # 系统工具与压缩
├── devtools/     # 开发工具
├── runtime/      # 语言运行时
├── terminal/     # 终端增强
├── network/      # 网络工具
├── media/        # 媒体处理
├── desktop/      # 桌面应用
├── ai/           # AI / LLM 工具
├── font/         # 字体
└── python/       # Python 生态
```

## 贡献

添加新包请提交 PR，manifest 格式参考现有 `.toml` 文件。

## 自动更新

仓库每天通过 GitHub Actions 检查一次 GitHub Releases 来源的 manifest。脚本只会在满足以下条件时自动更新并提交：

- 当前下载地址是 `github.com/<owner>/<repo>/releases/download/...`。
- 最新 release 能解析出新版本号。
- 最新 release 中存在与当前 Windows 资源同名模式匹配的 asset。
- 资源可以成功下载并计算 `sha256`。

无法安全判断的包会跳过并保留在 Actions 日志中，不会自动改写 manifest。
