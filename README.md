# 獎學金導航 Free 14

請先在電腦開啟 **START_HERE.html**，照 0～7 步操作。

GitHub Public + Cloudflare Workers Free / D1 / Workers AI + Tavily Free。
不使用 GitHub Pages、Render、Brave；不需安裝本機開發工具。

## 四個 Repository Secrets

- CLOUDFLARE_ACCOUNT_ID
- CLOUDFLARE_API_TOKEN
- TAVILY_API_KEY
- GH_DISPATCH_TOKEN

金鑰只能放在 Settings > Secrets and variables > Actions，不要上傳到程式檔。

## 上架順序

1. Actions > Deploy free website > Run workflow。完成後 Summary 有實際網址。
2. Actions > Collect and verify > Run workflow > verify。
3. 打開網站的 /check.html，看真實測試結果。
4. Collect and verify > daily，立即更新一次。之後每日台灣 12:00 排程。

## 已整合

既有 UI、學校與戶籍補查、全新公告發現、附件文字擷取、AI 整理與證據檢查、安全條件比對、進度與配額、寫入 D1、過期下架、個人資料本機儲存。

文字型 PDF / DOCX 可處理；掃描、長文、引用不完整、來源身分未核對或規定衝突會顯示待確認。不保證網路一筆不漏，不把機器整理說成人工或主辦審核。

## 工程測試

```sh
node --test tests/worker.test.mjs
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p test_pipeline.py
# UI tests need optional playwright + Chromium; not needed for deployment.
python tests/test_browser.py
```

外部成功回應為模擬；真正帳號測試請執行 verify。不需把任何真實個人資料放入 tests。
