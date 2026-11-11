# Ghi chú phân tích · Day 04

Họ và tên:
MSSV:

Cấu hình cả hai lần chạy: agent và judge đều là `openai/gpt-oss-120b` qua OpenRouter, snapshot 09/11/2026, `max_steps` 8, dataset core 22 case (khối `config` trong hai file giống nhau). Phân tích đầy đủ hơn (các vòng cải tiến sau, chạy lặp, judge thứ hai, bonus) ở [BAOCAO.md](BAOCAO.md).

## 1. Case được chọn

- ID case: **A5**: "Tôi cần danh sách đầy đủ tất cả incident GitHub Actions trong năm 2025. Hãy tìm dữ liệu và nói rõ độ đầy đủ của kết quả, đừng bịa."
- Câu trả lời của agent: một bảng các incident Actions tìm được (`t89g2q1800fr`, `h02n9572mx9y`, `xr3myzj7ms51`, `smtdswd1h2w4`, `pfbmpxxfq1cy`…). Câu trả lời nêu rõ dữ liệu chỉ gồm 50 incident gần nhất nên không phải danh sách đầy đủ năm 2025. Nội dung hợp lý nhưng chỉ có ID, **không có URL incident**.
- Điểm ở lần chạy 1: **0**, `status: max_steps`, reason của judge: "Agent hết số bước trước khi hoàn tất."

## 2. Bằng chứng trong trace

Trace của A5 trong `eval_before.json` (rút gọn, bỏ trường `result`):

```text
{"event": "model", "step": 1, "finish": "tool_calls", "tool_calls": 1}
{"event": "tool", "step": 1, "name": "get_status", "arguments": {"provider": "github", "component": "Actions"}, "ok": true}
{"event": "tool", "step": 2, "name": "get_incident_details", "arguments": {"provider": "github", "incident_id": "pfbmpxxfq1cy"}, "ok": true}
{"event": "tool", "step": 3, "name": "get_incident_details", "arguments": {"provider": "github", "incident_id": "t89g2q1800fr"}, "ok": true}
{"event": "tool", "step": 4, "name": "get_incident_details", "arguments": {"provider": "github", "incident_id": "h02n9572mx9y"}, "ok": true}
{"event": "tool", "step": 5, "name": "get_incident_details", "arguments": {"provider": "github", "incident_id": "xr3myzj7ms51"}, "ok": true}
{"event": "tool", "step": 6, "name": "get_incident_details", "arguments": {"provider": "github", "incident_id": "smtdswd1h2w4"}, "ok": true}
{"event": "tool", "step": 7, "name": "get_status", "arguments": {"provider": "github"}, "ok": true}
{"event": "model", "step": 8, "finish": "stop", "tool_calls": 0}
{"event": "validation_missing_source", "step": 8, "expected_any_of": ["https://stspg.io/s5bs6jn41fwh", "https://stspg.io/lv4q3xm9r0rk", ...]}
{"event": "end", "status": "max_steps", "error": "Hết 8 lần gọi model trước khi có câu trả lời hợp lệ."}
```

Nguyên nhân: model dùng 7 lượt đầu để gọi tool, mỗi lượt một call, và chỉ trả lời ở **lượt 8/8**. Câu trả lời thiếu URL nên bước kiểm tra nguồn chặn lại (`validation_missing_source`). Lúc đó không còn lượt để model sửa, nên run kết thúc `max_steps` và bị chấm 0. Lỗi nằm ở **loop**: agent không theo dõi số lượt còn lại, nên bước kiểm tra nguồn chỉ có tác dụng khi model tình cờ trả lời sớm. Model không biết mình sắp hết lượt.

## 3. Thay đổi đã thực hiện

- Sửa gì, ở đâu: trong `run_agent` của `agent.py`, khi chỉ còn ≤ 2 lượt gọi model (`BUDGET_WARNING_AT = 2`) mà message cuối là tool result (model vẫn đang điều tra), agent thêm một message `user` nhắc model: không gọi thêm tool, viết câu trả lời cuối, nêu phần chưa kiểm tra được và dẫn URL các incident đã đọc (danh sách URL đã quan sát được đưa vào lời nhắc). Agent ghi event `budget_warning` vào trace. Lượt áp chót dùng để trả lời, lượt cuối dự phòng cho bước kiểm tra nguồn. Test offline ở `tests/test_budget.py` (`BudgetWarningTests`): có lời nhắc khi còn 2 lượt, vẫn sửa được câu trả lời thiếu nguồn ở lượt cuối, và không nhắc khi còn nhiều lượt.
- Vì sao chọn thay đổi này: A5 là case duy nhất bị 0 điểm, và nguyên nhân là lỗi của loop chứ không phải của dữ liệu hay model. Thay đổi này áp dụng cho mọi câu hỏi cần nhiều bước, không ghi cứng gì về A5, và giữ nguyên contract (`length` hay lỗi khác vẫn không được tính là `completed`).

## 4. So sánh hai lần chạy

| Chỉ số | `eval_before.json` | `eval_after.json` |
|---|---|---|
| Điểm tổng | 89,32 | 94,09 |
| Điểm case đã chọn (A5) | 0 | 95 |
| Số model calls (agent) | 75 | 63 |
| Token, chi phí nếu có | 112.539 token; agent 0,0089 USD, judge 0,0075 USD | 75.343 token; agent 0,0065 USD, judge 0,0072 USD |

- Lỗi đã giải quyết: A5 không còn kết thúc `max_steps`. Tuy nhiên, **trong `eval_after.json` thì `budget_warning` không được kích hoạt ở case nào**: lần này A5 chỉ gọi 1 tool và trả lời ở bước 2. Vì vậy 0 → 95 không phải bằng chứng cho cải tiến. Để kiểm tra đúng cơ chế, mình chạy riêng A5 với `--max-steps 3`, mỗi phiên bản 3 lần. Trước khi sửa: 80, **0** (lặp lại đúng lỗi `max_steps` thiếu URL), 98; `budget_warning` 0/3. Sau khi sửa: 93, 85, 89; `budget_warning` 3/3, cả 3 lần đều hoàn thành. Khi chạy đủ 22 case 3 lần mỗi phiên bản, lỗi `max_steps` ở A5 xuất hiện 1/3 lần trước khi sửa và 0/3 lần sau khi sửa.
- Lỗi còn lại hoặc mới xuất hiện: H7 vẫn lỗi (55 → 37): Cloudflare timeout nhưng model làm theo yêu cầu ép của người dùng và khẳng định Cloudflare operational. A6 giảm (98 → 75): sau `validation_missing_source` model chỉ viết lại câu trả lời mà không mở incident liên quan. H2 tăng (50 → 98) nhưng không nhờ thay đổi này. Các lỗi đó được sửa ở vòng sau. `agent.py` nộp là bản cuối, kết quả ở `eval_after_v3.json` (97,32, A5 100). Chi tiết ở BAOCAO.md.

## 5. Giới hạn

- **Điểm judge:** judge dùng cùng model với agent nên có thể thiên lệch tự chấm, và judge có chấm sai. Ví dụ E4 ở vòng 2: judge cho rằng agent bịa "Code Scanning, Copilot", nhưng update của incident có ghi nguyên văn. Khi chấm lại 9 lần chạy bằng judge thứ hai (`google/gemini-2.5-flash`), điểm cao hơn khoảng 2–3 điểm nhưng thứ tự các phiên bản không đổi, và mỗi judge đều có lúc sai. Các case thấp cần được đối chiếu với trace.
- **Snapshot:** dữ liệu cố định ngày 09/11/2026 và chỉ có 50 incident gần nhất mỗi provider, nên "danh sách đầy đủ năm 2025" như A5 yêu cầu là không thể trả lời đầy đủ. Kết quả không phản ánh tình trạng thực tế và không kiểm tra được hành vi khi API thật bị rate limit hay thay đổi.
- **Chạy một lần mỗi phiên bản:** model không tất định. Ngay ở lần chạy chính thức, A5 không đi vào tình huống sắp hết lượt, và trước khi sửa thì cả bộ đã dao động 89,32–92,23 giữa 3 lần. Chênh lệch vài điểm giữa `eval_before` và `eval_after` có thể chỉ là nhiễu. Thí nghiệm `--max-steps 3` và các lần chạy lặp chỉ có 3 mẫu, nên mới là dấu hiệu, chưa chứng minh độ ổn định.
- **Phạm vi của kiểm tra nguồn:** chỉ xác nhận câu trả lời có ít nhất một URL incident đã quan sát, không chứng minh mọi khẳng định đều có nguồn. Ví dụ A1 ở `eval_after_v3.json` thiếu URL Cloudflare nhưng vẫn qua kiểm tra.
