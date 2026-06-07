# serp-search 插件安装说明

通过 **serp.hk** / **serp.global** 调用 Google 搜索，在 OpenClaw 里注册工具 `serp_search`。

## 方式一：命令行安装（推荐）

解压得到 `serp-search` 文件夹后，将整个目录打成 zip，或直接对 zip 执行：

```bash
openclaw plugins install /path/to/serp-search.zip
```

安装完成后**重启 Gateway**，再配置 API Key（见下文）。

## 方式二：手动解压

1. 解压 zip，得到文件夹 `serp-search`（内含 `package.json`、`index.ts`、`openclaw.plugin.json` 等）。
2. 把整个 `serp-search` 目录放到本机扩展目录（不要放进 OpenClaw 源码仓库里的 `extensions/`，而是用户配置目录）：
  ```text
   ~/.openclaw/extensions/serp-search/
  ```
3. 在插件目录安装依赖（**必须**，否则无法加载）：
  ```bash
   cd ~/.openclaw/extensions/serp-search
   npm install --omit=dev
  ```
4. **重启 OpenClaw Gateway**（或本机 menubar 里的 OpenClaw 应用）。

## 配置 API Key

在 OpenClaw 配置中为插件 `serp-search` 设置 `apiKey`，或设置环境变量 `SERP_API_KEY`（二选一即可）。

配置路径示例：`plugins.entries.serp-search.config.apiKey`

可选：`plugins.entries.serp-search.config.region` 为 `"cn"`（默认，走 serp.hk）或 `"global"`（走 serp.global）。

## 验证

```bash
openclaw plugins list
```

应能看到 `serp-search`；代理工具里可出现 **Google Search (serp.hk)** / `serp_search`。

## 安全提示

插件在 Gateway 进程内运行，请只安装你信任的代码。