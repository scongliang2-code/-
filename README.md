# 淘宝商品复制与上架工具

当前主程序为 Python 桌面工具，不需要 Node.js 或浏览器插件。

## 启动

双击 `启动商品复制工具.bat`。首次安装依赖：

```powershell
python -m pip install -r python_copy/requirements.txt
```

需要本机安装 Python（含 Tkinter）及 Google Chrome。填写商品链接、店铺配置和类目后，选择“仅填写检查”或“填写并自动上架”。具体设置见 [使用说明](python_copy/README.md)。

## 目录

- `python_copy/`：当前程序、配置示例和回归测试。`extract_product.js` 是 Python 采集器使用的浏览器脚本，必须保留。
- `启动商品复制工具.bat`：日常使用入口。
- `output/`：上次表单、任务配置、素材、截图、结果和历史发布记录。
- `.shop-profiles/`：当前工具按店铺保存的浏览器登录状态。
- `.python-copy-profile/`：旧样本 Python 入口的登录状态。
- `.record-browser-profile/`、`.taobao-browser-profile/`：历史浏览器登录资料，保留以免丢失登录。

不要把 `output/` 或浏览器登录目录上传到 GitHub。发布前核对实际商品信息；每次新建任务可能再次上架商品，历史记录请保留以便核对。

## 历史文件

2026-11-03 已将早期 Node.js 发布器、插件录制器、旧独立脚本、临时 GitHub 副本及调试缓存移到 `output/archive/retired-20261103/`，日常运行不需要它们。移动清单是 `output/archive/moved-20261103.json`。旧源文件另有 ZIP 备份及 SHA-256 校验清单。

这些文件是归档保留，并未永久删除。恢复旧工具时按移动清单放回原位置；不要用旧归档覆盖当前 `python_copy/`。

## 验证

```powershell
python -B -X utf8 -m unittest discover -s python_copy -p "test_*.py"
```

测试通过不代表所有淘宝类目均已完成真实上架验证。
