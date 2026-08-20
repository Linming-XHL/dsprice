# DeepSeek API 价格峰谷指示器

一个轻量的桌面托盘工具，实时显示 DeepSeek API 当前所处的计费时段（高峰/低谷），并用颜色直观提示。

基于 **Python + pystray + Tkinter**，跨平台支持 Windows 与 Linux。

## 功能

- 启动后驻留在系统托盘（任务栏角标），点击托盘图标即可弹出/隐藏界面
- 界面显示：
  - 当前日期、时间、星期
  - 当前时段剩余时间
  - 当前所处时段的大字提示：高峰 **梁文峰**（红色）/ 低谷 **梁文谷**（绿色）
  - 当前时段剩余 **5 分钟以内** 时变黄提示
  - **下方一行显示账户余额**（调用 DeepSeek 官方查询余额 API）
- 托盘图标颜色随时段实时变化（红 / 绿 / 黄）
- 右上角小钥匙图标：配置 API Key（加密后保存在用户目录）
- 首次启动自动弹出 API Key 配置窗口；可跳过（余额功能不可用）
- **主窗口与配置窗口默认定位在桌面右下角**，每次显示都会重新对齐右下角

## 余额功能

- 接口：`GET https://api.deepseek.com/user/balance`
- API Key 使用 `cryptography` 加密后存储在用户目录 `~/.dsprice/config.json`
- 支持在配置窗口选择刷新周期：**30 秒 / 1 分钟 / 5 分钟**
- 请求失败或密钥无效时余额行显示为红色错误信息，并每 30 秒自动重试
- 托盘菜单中提供「刷新余额」「配置 API Key」入口（可手动立即刷新）

## 峰谷时段规则

DeepSeek V4 API 采用峰谷定价（空闲时段价格为高峰时段的一半），**高峰时段为北京时间每日 9:00–12:00、14:00–18:00**（不分工作日/周末），其余时间为低谷（空闲）时段。

低谷时段剩余时间指距下一个高峰时段开始的时间。

## 环境要求

- Python 3.8+
- Windows 或 Linux 桌面环境（Linux 需要系统托盘支持，如 GNOME/KDE/AppIndicator）

## 安装

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
```

> 创建虚拟环境时加上 `--system-site-packages`：Linux 下托盘需要系统自带的 PyGObject(`gi`)、DBus 等库。

Linux 额外依赖（系统包）：

```bash
# Debian / Ubuntu
sudo apt install python3-tk python3-gi gir1.2-appindicator3-0.1
```

- `python3-tk`：Tkinter 界面
- `python3-gi` + `gir1.2-appindicator3-0.1`：Wayland/X11 下的系统托盘支持（AppIndicator）

## 运行

```bash
.venv/bin/python app.py
```

> 说明：
> - Linux（AppIndicator）下左键点击托盘图标弹出菜单，菜单中有「显示/隐藏」；Windows 下左键点击托盘图标直接切换显示/隐藏。
> - 若系统缺少托盘支持（如 GNOME 未安装 AppIndicator 扩展），程序会自动回退为直接显示主窗口。

## 打包为可执行文件（可选）

GitHub Actions 已配置 `.github/workflows/build-release.yml`，推送到 `main` 或手动触发即可自动用 PyInstaller 打包 Windows（`.exe`）与 Linux 二进制，并以北京时间时间戳（如 `20260820T142035`）发布 Release。

本地手动打包参考：

```bash
pip install pyinstaller
pyinstaller -F -w -n dsprice --collect-submodules cryptography app.py   # Windows
pyinstaller -F -n dsprice --collect-submodules cryptography --exclude-module gi app.py   # Linux
```

> 说明：Linux 打包版未捆绑 GTK/AppIndicator，托盘在无 X11 的系统上会自动回退为直接显示主窗口。

## 使用说明

- 启动即显示主窗口（默认右下角）；点击关闭按钮隐藏到托盘，程序继续在后台运行
- Windows：左键单击托盘图标即可显示/隐藏主界面；右键菜单含「显示/隐藏」「退出」
- Linux（AppIndicator）：点击托盘图标弹出菜单，选择「显示/隐藏」切换主界面，或「退出」结束程序

## 项目结构

```
.
├── app.py            # 主程序
├── requirements.txt  # Python 依赖
└── LICENSE
```
