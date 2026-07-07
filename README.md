# 台灣可轉債 CB 戰術監控

Streamlit 即時監控台灣可轉債，整合 TPEx 掛牌庫、新定價案爬蟲、FinLab/yfinance 現股報價，並標示強勢續拉、疑似壓價、觀察標的與回測勝率。

## 本機執行

```powershell
cd D:\Program\stock\CB
D:\Program\venv\Scripts\pip.exe install -r requirements.txt
D:\Program\venv\Scripts\streamlit.exe run CB.py
```

如果 `D:\Program\venv\Scripts\python.exe` 無法啟動，先重建或修復 venv，再安裝依賴。

## FinLab Token 設定

發布時不要把 token 寫進 `CB.py`。

本機可建立 `.streamlit/secrets.toml`：

```toml
FINLAB_API_TOKEN = "你的 FinLab token"
```

也可以用環境變數：

```powershell
$env:FINLAB_API_TOKEN="你的 FinLab token"
streamlit run CB.py
```

## Streamlit Cloud 發布

1. 把這個資料夾推到 GitHub。
2. 建立 Streamlit app。
3. Main file path：
   - 如果 GitHub repo 根目錄就是 `CB` 資料夾，填 `CB.py`。
   - 如果 GitHub repo 根目錄是 `D:\Program\stock` 這層，填 `CB/CB.py`。
4. 在 Streamlit Cloud 的 Secrets 填：

```toml
FINLAB_API_TOKEN = "你的 FinLab token"
```

5. Deploy。

專案已附 `runtime.txt`，建議雲端使用 Python 3.11，避免 FinLab 在太新的 Python 版本上出現相容性問題。

## 發布前檢查

```powershell
python -c "from pathlib import Path; p=Path('CB.py'); compile(p.read_text(encoding='utf-8-sig'), str(p), 'exec'); print('syntax ok')"
```

確認 `.streamlit/secrets.toml` 沒有被提交。
