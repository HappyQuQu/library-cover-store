# Library Cover Store · 封面模板商店

[Library Cover Studio](https://github.com/HappyQuQu/library-cover-studio) 的公开社区模板仓库。在应用「模板商店」中预览、下载社区设计，一键加入自己的模板库。

模板使用与应用导出 ZIP 相同的 JSON 和预览图。每套模板独立打包，独立累计全站下载次数；商店更新不会覆盖用户已经编辑的本地模板。

## 首批社区模板

七套「试作 ·」封面设计均为 1920 × 1080，可在设计器中继续编辑：

| 模板 | 设计特点 | 海报数量 |
| --- | --- | --- |
| [票根收藏](票根收藏/) | 电影票纸面、齿孔、编号与条码装饰 | 1 |
| [胶片放映](胶片放映/) | 横向四格胶片与上下齿孔 | 4 |
| [黑胶唱片](黑胶唱片/) | 唱片纹理与圆形封面 | 3 |
| [影像画册](影像画册/) | 杂志式主图、双图与图注编排 | 3 |
| [轨道星图](轨道星图/) | 五张海报沿轨道环绕中央标题 | 5 |
| [四幕蒙太奇](四幕蒙太奇/) | 四角画面拼接与中央标题带 | 4 |
| [蜂巢拼图](蜂巢拼图/) | 七张六边形海报组成蜂巢 | 7 |

这些模板通过商店下载添加，不属于应用首次启动的默认模板。使用圆形或六边形海报裁切时，请更新到包含相对蒙版渲染支持的应用版本；较早版本可能在自动化生成时显示为矩形。

## 使用模板

1. 在 Library Cover Studio 打开「模板商店」。
2. 搜索模板、查看预览和尺寸，点击「下载并添加」。
3. 下载成功后，模板立即出现在「模板库」、设计器及自动化任务的模板菜单中。
4. 已添加同一版本时直接「打开模板」。商店发布新版本后可以另外添加，保留旧模板及其修改。

也可以在 [Releases](https://github.com/HappyQuQu/library-cover-store/releases) 下载单套模板 ZIP，在应用「模板库 → 导入模板」中手动导入。不要使用 GitHub 自动生成的整个仓库 Source code ZIP。

## 提交模板

### 1. 从应用导出

在应用「模板库」找到设计，点击「导出」。解压下载的 ZIP，得到 `template.json` 和 `preview.jpg`。

### 2. 建立模板文件夹

Fork 本仓库，在仓库根目录创建一个有辨识度的文件夹，将解压后的两个文件放进去：

```text
library-cover-store/
├── 胶片放映/
│   ├── template.json
│   └── preview.jpg
├── 票根收藏/
│   ├── template.json
│   └── preview.jpg
├── scripts/
└── .github/
```

- 一套模板一个文件夹，不要提交合集，也不要把 ZIP 放进源码仓库。
- JSON 必须叫 `template.json`；预览图必须是 `preview.jpg`、`preview.jpeg`、`preview.png` 或 `preview.webp`，只保留其中一张。
- **文件夹名称是模板的稳定身份**。更新时修改原文件夹里的文件，不要重命名文件夹，否则会作为新模板统计下载次数。
- JSON 的 `name` 是商店显示名称，可以修改。`description` 可以写作者、设计特点、海报数量、字体及最低应用版本等说明。
- JSON 与预览图合计不超过 **5 MB**；画布宽度为 100–4000、高度为 100–6000，名称不超过 100 字符。
- 建议使用应用导出的文件，不要把 `canvasJson` 从字符串改成对象。预览图应与模板内容一致。
- 自定义字体需要在描述中说明安装方法；当前导出格式不包含字体或其他外部资源，请确保设计能正常移植。
- 不要上传 API Key、账号密码、媒体服务器地址或其他私人信息。
- 本仓库采用 [MIT License](LICENSE)。请确保你有权以该许可分享模板及预览素材。

JSON 的结构示例（完整设计请从应用导出）：

```json
{
  "name": "胶片放映",
  "description": "四格胶片封面，使用四张海报。作者：你的名字。",
  "width": 1920,
  "height": 1080,
  "canvasJson": "{\"version\":\"6.0.0\",\"objects\":[]}",
  "isPublic": false
}
```

### 3. 校验并发起 Pull Request

安装 Python 3.10 或更高版本，无需额外依赖：

```sh
python3 scripts/publish.py check
python3 -m unittest discover -s tests -v
```

想在本机检查生成的 ZIP：

```sh
python3 scripts/publish.py build
```

ZIP 生成在 `.store-build/` 中，此目录已被 Git 忽略。将单套 ZIP 导入应用，确认预览和渲染正常。

提交模板文件夹并向本仓库的 `main` 分支发起 Pull Request。PR 描述请写明模板名称、设计特点、所需字体以及测试使用的应用版本。自动校验通过、维护者合并后，工作流会自动上架。

只发布根目录中的模板文件夹。以 `.` 开头的目录以及 `scripts`、`tests`、`doc`、`docs`、`examples`、`node_modules` 是保留目录，不用于放模板。

## 自动发布和下载统计

维护者不需要逐个手工创建 ZIP 或 Release：

1. `main` 更新后，GitHub Actions 校验模板并计算内容版本。
2. **只为新增或修改的模板生成 ZIP 并上传**；未变化的模板复用原附件。
3. ZIP 分批存放在 `store-00001`、`store-00002` 等 Release 中，每批最多 **200 个版本附件**，满后自动开下一批。每个 ZIP 始终只包含一套模板。
4. 旧版本附件保留。每套模板的下载次数是其所有已发布版本附件的 GitHub `download_count` 之和。
5. 工作流更新 `catalog` 分支的 `catalog.json`。应用读取这份轻量目录，分页显示 24 套模板，下载时才获取对应 ZIP。

目录和统计约 **每小时**同步一次，GitHub 调度可能延迟，应用还缓存目录 5 分钟。「下载并添加」会获取一次 Release ZIP，重复打开已添加版本不再下载。次数是附件获取次数，不是唯一用户数，也不等于成功导入人数。

为控制 API 写入速率，每次工作流最多上传 **100 个新版本**，依次限速。首次导入数千套模板会分多次执行；未完成的模板在下一次每小时同步时继续发布。失败重试会复用已上传附件。排队的新模板暂不上架；排队更新的模板继续提供已发布旧版本。

删除模板文件夹会将其从商店下架，历史附件仍保留；重新添加相同文件夹可继续累计历史次数。请勿手工删除、覆盖或重命名托管的 Release 附件，否则历史计数可能丢失。

## 维护者首次启用

- 将源码及 `.github/workflows/store.yml` 推送到 GitHub 的 `main` 分支。
- 确认 Actions 已启用，仓库规则允许工作流以 `contents: write` 权限更新 `catalog` 分支并创建 Release。使用 GitHub 自动提供的 Token，应用无需个人 Token。
- 在 Actions 中运行一次 **Validate and publish template store**，或提交第一套模板触发发布。
- `catalog` 分支和 Release 自动生成，不要手工维护 `catalog.json`。没有模板时首次运行也会生成空目录。
- 定时 Actions 长时间无活动可能被 GitHub 停用；如统计停止更新，在 Actions 页面重新启用。发布失败时修复后重新运行，已上传版本会保留。

应用读取的目录：[catalog.json](https://raw.githubusercontent.com/HappyQuQu/library-cover-store/catalog/catalog.json)。欢迎提交模板、反馈问题或完善贡献说明。
