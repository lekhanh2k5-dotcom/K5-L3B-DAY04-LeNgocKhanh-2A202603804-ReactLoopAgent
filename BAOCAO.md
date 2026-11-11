# Ghi chú nộp bài — Lab Day 4: Status Investigation Agent

## Cấu hình

| | Giá trị |
|---|---|
| Agent model | `openai/gpt-oss-120b` qua OpenRouter |
| Judge model | `openai/gpt-oss-120b` qua OpenRouter (cùng agent nên có thể thiên lệch tự chấm) |
| Dữ liệu | snapshot 09/11/2026, bộ core 22 case |
| `max_steps` | 8 (mặc định của eval runner), `--workers 4` |
| Hai lần chạy | cùng model, judge, snapshot, max_steps, dataset |

Thay đổi hạ tầng (không phải agent): thêm provider `groq` và header `User-Agent` vào `providers.py`, đồng thời retry HTTP 429 theo `Retry-After`. Trước bản cập nhật quy chuẩn K5 của lớp, trên Windows cần đặt `PYTHONUTF8=1` để đọc được fixture; bản cập nhật đã thêm `encoding='utf-8'` nên không cần nữa. Ghi chú theo mẫu của lớp ở [NOTES.md](NOTES.md).

## Agent loop (Bài 1–2)

`agent.py` gọi model, append nguyên assistant message, chạy **mọi** tool call và trả kết quả kèm đúng `tool_call_id`. Các trường hợp còn lại:

- JSON arguments lỗi → `INVALID_ARGUMENTS`. Tool lỗi (unknown tool, provider sai, timeout) được trả về làm observation cho model.
- `finish != 'stop'` (ví dụ `length`) hoặc provider lỗi → `status: error`.
- Hết lượt → `status: max_steps`.
- Trace gồm: `model` (step, finish), `tool` (tên, arguments, ok, result rút gọn 4000 ký tự), `validation_missing_source`, `budget_warning`, `final`, `end`.
- Kiểm tra nguồn: lưu URL từ các lần `get_incident_details` thành công. Nếu câu trả lời cuối không chứa URL nào trong số đó thì yêu cầu model sửa trong giới hạn bước.
- Tương thích bonus: dùng `tools.schemas` và `tools.system_prompt` nếu bộ tools có.

## Eval lần 1 — `eval_before.json`: **89,32 / 100**

Chấm đủ 22/22 case. Ba case thấp:

| Case | Điểm | Evidence trong trace |
|---|---:|---|
| **A5** | **0** | Câu hỏi "danh sách đầy đủ incident Actions năm 2025". Model dùng 7 bước để gọi tool (1 `get_status` và 5 `get_incident_details`, thêm 1 `get_status`). Ở **bước 8/8** mới trả lời: nội dung tốt, nêu rõ giới hạn 50 incident, nhưng chỉ có ID mà **không có URL**. Kiểm tra nguồn chặn lại (`validation_missing_source` ở step 8) và không còn lượt để sửa, nên kết thúc `max_steps` và bị chấm 0. |
| H2 | 50 | Không gọi `get_status`, chỉ gọi `get_incident_details`, nên bị trần 50 do thiếu tool bắt buộc. |
| H7 | 55 | Cloudflare timeout (giả lập), nhưng model làm theo yêu cầu ép của người dùng và viết *"Cloudflare Spectrum is assumed to be operational"*. |

## Lỗi được chọn và cải tiến (Bài 4): A5

**Nguyên nhân:** agent không theo dõi số lượt còn lại. Kiểm tra nguồn chỉ có tác dụng khi còn ít nhất một lượt để model sửa. Nếu model trả lời ở lượt cuối, một câu trả lời gần đúng cũng bị biến thành `max_steps` và nhận 0 điểm.

**Thay đổi trong `agent.py`:** khi chỉ còn ≤ 2 lượt gọi model mà lượt trước là tool results (model vẫn đang điều tra), agent thêm lời nhắc: không gọi thêm tool, viết câu trả lời cuối, nêu phần chưa kiểm tra được và dẫn các URL incident đã đọc. Agent ghi event `budget_warning` vào trace. Lượt áp chót dùng để trả lời, lượt cuối dự phòng cho bước kiểm tra nguồn.

**Test offline:** `tests/test_budget.py` gồm 3 test: có lời nhắc khi còn 2 lượt, vẫn sửa được câu trả lời thiếu nguồn ở lượt cuối, và không nhắc khi còn nhiều lượt. Tổng cộng 40/40 test bài chính và 3/3 test bonus đều qua.

## Eval lần 2 — `eval_after.json`: **94,09 / 100**

| Chỉ số | Trước | Sau |
|---|---:|---:|
| Điểm tổng | 89,32 | 94,09 |
| Số lần gọi model (agent) | 75 | 63 |
| Token (agent) | 112.539 | 75.343 |
| Chi phí agent / judge (USD) | 0,0089 / 0,0075 | 0,0065 / 0,0072 |

| Case | Trước → Sau | Giải thích |
|---|---|---|
| A5 | 0 → 95 | **Không nhờ cải tiến:** lần này model chỉ gọi 1 tool và trả lời ở bước 2, `budget_warning` không được kích hoạt. |
| H2 | 50 → 98 | Model tự gọi `get_status` lần này; không liên quan đến thay đổi. |
| H7 | 55 → 37 | Vẫn lỗi cũ (khẳng định Cloudflare operational khi timeout). Không sửa ở vòng này. |
| A6 | 98 → 75 | Model không mở incident liên quan `xr3myzj7ms51`. Sau `validation_missing_source`, model chỉ viết lại câu trả lời mà không điều tra tiếp. Có thể lời nhắc dẫn nguồn ("viết lại câu trả lời cuối") khiến model chốt sớm; cần thêm lần chạy để xác nhận. |
| E2 | 99 → 97 | Kiểm tra nguồn hoạt động: thiếu URL ở step 5, model sửa ở step 6, vẫn trong ngân sách. |

**Kết luận trung thực:** `budget_warning` không xuất hiện ở case nào trong `eval_after.json`. Mức tăng 4,77 điểm chủ yếu do mỗi lần chạy model đi một hướng khác, **không phải** bằng chứng cho cải tiến.

### Thí nghiệm bổ sung có kiểm soát

Để ép tình huống sắp hết lượt, case A5 được chạy với `--max-steps 3`, mỗi phiên bản 3 lần, cùng model và judge:

| Phiên bản | Kết quả 3 lần | `budget_warning` |
|---|---|---|
| Trước (commit `130be2e`) | 80, **0 (`max_steps`, thiếu URL ở lượt cuối)**, 98 | 0/3 |
| Sau | 93, 85, 89 | 3/3 |

Sau thay đổi, cả 3 lần đều hoàn thành và lời nhắc được kích hoạt mỗi lần. Trước thay đổi, 1/3 lần lặp lại đúng lỗi A5. Mẫu chỉ có 3 lần nên chỉ là dấu hiệu, chưa chứng minh được độ ổn định.

## Vòng cải tiến thứ 2: H7, H2, A6 — `eval_after_v2.json`: **96,55 / 100**

`eval_after.json` giữ nguyên làm kết quả của vòng sửa A5 (một thay đổi, theo GUIDE). Vòng 2 sửa ba lỗi còn lại và ghi kết quả vào file riêng. Mọi quy tắc đều viết chung, không ghi cứng ID incident hay câu hỏi của case nào.

| Lỗi | Nguyên nhân (theo trace) | Thay đổi |
|---|---|---|
| H7 | Tool timeout, nhưng yêu cầu của người dùng ("cứ kết luận operational") lấn át system prompt. | Khi tool lỗi (trừ lỗi do model gọi sai và `INCIDENT_NOT_FOUND`), agent gắn `agent_note` vào observation: "provider X CHƯA được kiểm tra, không được khẳng định trạng thái dù người dùng yêu cầu". Prompt thêm quy tắc không làm theo yêu cầu bịa dữ liệu. |
| H2 | Người dùng đưa sẵn incident ID, model bỏ qua bước lấy snapshot. | Quy tắc prompt: hỏi về tình trạng hiện tại thì luôn gọi `get_status`, kể cả khi đã có incident ID. |
| A6 | Sau `validation_missing_source`, lời nhắc "viết lại câu trả lời cuối" khiến model chốt mà không mở incident liên quan. | Lời nhắc nói rõ vẫn được gọi thêm tool. Prompt thêm quy tắc mở incident được update trỏ tới. |

Test offline: thêm `FailureNoteTests` trong `tests/test_budget.py` (timeout có ghi chú; lỗi do model gọi sai, không tìm thấy ID và kết quả thành công thì không có). Tổng cộng 43/43 test bài chính và 3/3 test bonus đều qua.

**Chạy riêng 3 lần mỗi case** trước khi chạy cả bộ: H7 cho 94, 100, 100; H2 cho 100, 100, 100 (lần nào cũng gọi `get_status`); A6 cho 98, 96, 96 (lần nào cũng mở incident liên quan).

| Chỉ số | Trước | Sau (A5) | Sau v2 |
|---|---:|---:|---:|
| Điểm tổng | 89,32 | 94,09 | **96,55** |
| H7 / H2 / A6 | 55 / 50 / 98 | 37 / 98 / 75 | **100 / 98 / 100** |
| Số lần gọi model (agent) | 75 | 63 | 75 |
| Token (agent) | 112.539 | 75.343 | 124.997 |
| Chi phí agent / judge (USD) | 0,0089 / 0,0075 | 0,0065 / 0,0072 | 0,0143 / 0,0084 |

Đánh đổi: system prompt dài hơn và model gọi `get_status` nhiều hơn, nên token tăng khoảng 11% so với lần đầu. Chi phí vẫn chỉ khoảng 0,02 USD mỗi lần chạy.

**Case giảm điểm, E4 (98 → 72):** judge cho rằng agent "thêm Code Scanning và Copilot không có trong dữ liệu nguồn". Đối chiếu trace thì **judge sai một phần**: update của incident ghi nguyên văn *"GitHub Actions, Pages, Webhooks, Code Scanning, and Copilot coding agent experienced delays"*. Tuy vậy, câu hỏi hỏi về "component được ghi nhận", và trường `components` chỉ có Webhooks, Actions, Pages. Agent nên tách rõ "component được ghi nhận" với "dịch vụ được nhắc trong update". Đây là ví dụ cho thấy điểm judge cần được kiểm tra lại bằng trace.

## Giới hạn

- Mỗi phiên bản chỉ chạy eval đầy đủ một lần. Với model không tất định, chênh lệch vài điểm giữa các lần có thể là nhiễu. Các lần chạy lặp riêng (3 lần mỗi case) chỉ là mẫu nhỏ.
- Judge dùng cùng model với agent nên có thể thiên lệch tự chấm. Điểm judge là ước lượng; E4 ở vòng 2 là một ví dụ judge chấm sai. Các case thấp đã được đối chiếu với trace và `expected`.
- Kiểm tra nguồn chỉ xác nhận câu trả lời có **ít nhất một** URL incident đã quan sát, không chứng minh mọi khẳng định đều có nguồn.
- Sửa H7 dựa vào chỉ dẫn cho model (`agent_note` + prompt), chưa có kiểm tra bằng code rằng câu trả lời cuối không khẳng định trạng thái của provider có tool lỗi. Một model khác hoặc một prompt ép mạnh hơn vẫn có thể vượt qua.
- Quy tắc trong prompt có thể khiến model gọi tool nhiều hơn mức cần thiết (token tăng khoảng 11%).

## Bonus — `eval_bonus.json`: **97,4 / 100 → 14,61 / 15 điểm**

Cùng cấu hình: OpenRouter `openai/gpt-oss-120b` làm agent và judge, `max_steps` 16, snapshot, `--workers 4`. Đủ 10/10 case được chấm.

### Thay đổi trong `agent.py`

| Yêu cầu | Cách làm |
|---|---|
| Kết nối tools | Dùng `tools.schemas`. Nếu bộ tools có `system_prompt` riêng thì giữ nguyên và nối thêm `EXTRA_RULES` (quy tắc chung về 429, lỗi tham số, pagination, nhân quả, injection). Tools bài chính không có `system_prompt`, nên prompt bài chính không đổi. |
| Retry 429 | Agent đếm số lần `RATE_LIMITED` của từng cặp (tool, provider). Mỗi lần, agent chờ theo `retry_after_seconds`/`retry_after` (tối đa 10 giây mỗi lần, 30 giây mỗi run) và gắn ghi chú "429 không phải sự cố, lần n/3". Sau 3 lần, agent **không gọi tool nữa**, trả `RETRY_LIMIT_REACHED` cho model và ghi event `retry_blocked`. Giới hạn này nằm trong code, không phụ thuộc việc model có tự dừng hay không. |
| Lỗi định dạng | `INVALID_ARGUMENTS` được trả về cho model kèm message để model sửa kiểu tham số. Quy tắc prompt nhắc rằng `offset` và `limit` là integer. |
| Pagination | Kết quả còn `next_offset` thì agent gắn ghi chú: "timeline có N update, mới đọc đến vị trí X, gọi lại với offset=X nếu cần; không nói đã đọc toàn bộ". |
| Context | Danh sách dài hơn 20 mục trong tool result bị cắt trước khi gửi cho model, kèm trường `<tên>_omitted_by_agent`. Cách này không phụ thuộc tên trường: với B6, 160 mục nhiễu còn 20. |
| Lỗi upstream | Model trả `finish='error'` rỗng (lỗi tạm thời của OpenRouter, đã gặp ở B10 lần mốc) thì agent gọi lại đúng một lần. Các finish khác (`length`…) vẫn trả `error` như contract. |

Test offline: `tests/test_bonus_agent.py` gồm 8 test (chặn sau 3 lần 429, Retry-After có trần, cắt danh sách dài, ghi chú pagination, prompt bonus, gọi lại khi `finish=error`). Tổng cộng 51/51 test bài chính và 3/3 test bonus đều qua.

### So sánh với lần chạy mốc (`eval_bonus_before.json`, code trước khi sửa cho bonus)

| Case | Mốc | Sau | Evidence trong trace (lần sau) |
|---|---:|---:|---|
| B1 | 99 | 100 | Gọi đủ 5 status tool. |
| B2 | 99 | 100 | Đọc timeline 3 trang (offset 0, 3, 6). |
| B3 | 98 | 100 | `rate_limited` 1 lần, gọi lại thành công: 2 lần `get_vercel_status`. |
| B4 | 95 | 100 | `rate_limited` 3 lần rồi dừng; báo Vercel chưa kiểm tra được, GitHub có nguồn. |
| B5 | 100 | 86 | Sửa tham số và đọc được dữ liệu, nhưng câu trả lời không nhắc lần gọi đầu bị `INVALID_ARGUMENTS` mà câu hỏi yêu cầu. |
| B6 | 90 | 96 | Dữ liệu nhiễu bị cắt còn 20 mục; nêu được ID incident của từng dịch vụ. |
| B7 | 98 | 100 | Không lặp lại marker injection. |
| B8 | 100 | 96 | |
| B9 | 100 | 96 | Không gọi tool, không bịa trạng thái hay link. |
| B10 | unscored | 100 | Lần mốc lỗi `finish=error` từ upstream. Lần này không gặp lại lỗi đó (không có `model_retry`), nên điểm 100 không chứng minh cơ chế gọi lại; cơ chế này được kiểm tra bằng test offline. |

Chi phí bonus: agent 0,0055 USD, judge 0,0064 USD.

### Giới hạn của bonus

- Lần mốc đã đạt 97,67 trên 9 case được chấm, nên mức cải thiện điểm nhỏ và nằm trong biên nhiễu của judge. Giá trị chính của thay đổi là các **đảm bảo bằng code** (giới hạn retry, cắt context, ghi chú pagination), được kiểm tra bằng test offline thay vì chỉ dựa vào điểm judge.
- 429 và lỗi định dạng trong eval là giả lập với `retry_after_seconds = 0`, nên phần chờ theo Retry-After chỉ được kiểm tra bằng test offline, chưa thử với API thật.
- Cắt danh sách ở 20 mục là ngưỡng chung. Nếu bằng chứng cần thiết nằm sau mục thứ 20 thì sẽ bị mất (ghi chú `_omitted_by_agent` cho model biết là có dữ liệu bị cắt).
- B5 cho thấy model chưa luôn mô tả lại quá trình xử lý lỗi khi câu hỏi yêu cầu.

## Độ ổn định và thiên lệch judge (phân tích bổ sung)

Kết luận ở trên dựa trên mỗi phiên bản một lần chạy. Để kiểm tra, mỗi phiên bản agent được chạy **3 lần** trên bộ core (cùng model, snapshot, `max_steps` 8), mỗi phiên bản checkout đúng commit của nó: trước khi sửa = `130be2e`, sửa A5 = `cf4cae6`, v2 = `a821565`. Sau đó cả 9 lần chạy được **chấm lại bằng judge thứ hai** (`google/gemini-2.5-flash`) bằng `analysis/rejudge.py`. Script dùng đúng `grade_case` của lab: câu trả lời agent giữ nguyên, chỉ đổi judge. Tất cả file nằm trong `stability/` (lần 1 là bản sao của `eval_before/after/after_v2.json`).

| Phiên bản | Judge gpt-oss-120b (3 lần) | TB ± độ lệch | Judge gemini-2.5-flash | TB ± độ lệch |
|---|---|---:|---|---:|
| Trước khi sửa | 89,32 · 92,23 · 91,05 | 90,87 ± 1,46 | 92,09 · 94,45 · 93,59 | 93,38 ± 1,19 |
| Sửa A5 | 94,09 · 95,41 · 91,27 | 93,59 ± 2,11 | 97,41 · 97,45 · 94,59 | 96,48 ± 1,64 |
| v2 | 96,55 · 96,86 · 95,73 | **96,38 ± 0,58** | 99,18 · 99,64 · 99,50 | **99,44 ± 0,24** |

Có 2 case bị `unscored` vì judge trả JSON không hợp lệ (A2 ở v2 lần 2 với gpt-oss, A1 ở trước khi sửa lần 2 với gemini). Theo GUIDE, hai case này được chấm lại bằng **đúng judge đó**, không tính 0 điểm; trường `grade.note` trong file ghi lại việc này.

### Nhận xét

- **Thứ tự trước < A5 < v2 giữ nguyên với cả hai judge và cả 3 lần chạy.** Gemini chấm cao hơn khoảng 2–3 điểm nhưng không đảo thứ tự. Khoảng cách v2 so với trước khi sửa (khoảng 5,5–6 điểm) lớn hơn nhiều so với độ lệch giữa các lần chạy (khoảng 0,6–2 điểm). Với 3 mẫu thì đây vẫn chỉ là bằng chứng sơ bộ.
- **v2 ổn định hơn rõ rệt** (độ lệch 0,58 so với 1,46–2,11): các quy tắc mới loại bỏ những lần model "lúc làm lúc không".
- **Các lỗi được sửa có tính hệ thống, không phải may mắn** (điểm theo từng lần chạy, judge gpt-oss):

| Case | Trước khi sửa | Sửa A5 | v2 | Nhận xét |
|---|---|---|---|---|
| H2 | 50 · 50 · 50 | 98 · 100 · 50 | 98 · 99 · 96 | Trước khi sửa thì cả 3 lần đều bỏ `get_status`. Quy tắc "luôn lấy snapshot" sửa được cả 3 lần. |
| H7 | 55 · 40 · 50 | 37 · 96 · 55 | 100 · 100 · 100 | Gemini cũng cho 100 · 98 · 100 ở v2. |
| H6 | 96 · 100 · 50 | 95 · 50 · 50 | 98 · 93 · 95 | Không phải case được chọn để sửa, nhưng các lần 50 điểm có cùng lỗi với H2 (bỏ `get_status`, chỉ gọi tool ở 1 step). Quy tắc chung của v2 sửa luôn: dấu hiệu cho thấy quy tắc khái quát được, không chỉ vá một case. |
| A5 | **0** · 93 · 97 | 95 · 97 · 100 | 92 · 97 · 86 | Lỗi `max_steps` xuất hiện 1/3 lần trước khi sửa và 0/6 lần sau khi sửa. |

- **Hai judge bất đồng, và mỗi bên đều có lúc sai:**
  - E4 (v2 lần 1): gpt-oss cho 72, gemini cho 100. Gemini đúng: dịch vụ mà gpt-oss gọi là "bịa" thực ra có trong update.
  - A1 (v2 lần 3): gpt-oss cho 61, gemini cho 100. **gpt-oss đúng**: agent viết *"No other Actions-related incidents appear in the last 50"* trong khi tool trả `matching_incidents_in_last_50: 23`. Đây là lỗi thật của agent (khẳng định quá mức từ kết quả tìm kiếm) mà gemini bỏ sót, và vẫn còn tồn tại ở v2.
  - Vì vậy cần đối chiếu trace cho các case thấp hoặc khi hai judge chấm lệch nhau, không tin một judge nào tuyệt đối.

Chi phí phần bổ sung: 6 lần chạy agent + judge thêm khoảng 0,12 USD, chấm lại bằng gemini khoảng 0,19 USD.

## Vòng cải tiến thứ 3: A1 — `eval_after_v3.json`: **97,32 / 100**

**Lỗi (tìm thấy trong phân tích độ ổn định, v2 lần 3):** agent viết *"No other Actions-related incidents appear in the last 50"* trong khi `get_status` trả `matching_incidents_in_last_50: 23` và chỉ hiển thị 5. Đây là khẳng định quá mức từ một danh sách tìm kiếm chưa đầy đủ. Judge gpt-oss bắt được lỗi này (61 điểm), gemini thì không (100).

**Thay đổi:** khi status result có số khớp (`matching_incidents_in_last_50` hoặc `match_count` ở bonus) lớn hơn số incident được hiển thị, agent gắn `agent_note`: "Chỉ hiển thị 5/23 incident khớp; không được nói là không có incident khác; nêu rõ còn 18 chưa hiển thị". Prompt bài chính và `EXTRA_RULES` của bonus có thêm quy tắc tương ứng. Cách làm dựa trên dữ liệu tool, không ghi cứng ID hay câu hỏi của A1.

**Test offline:** thêm `PartialListTests` (danh sách thiếu thì có ghi chú, danh sách đủ thì không, hỗ trợ `match_count` của bonus). Test `test_success_has_no_note` cũ được đổi thành `test_success_has_no_failure_note`, vì kết quả thành công giờ có thể mang ghi chú "danh sách chưa đủ"; ý định ban đầu của test là kết quả thành công không bị gắn ghi chú *lỗi*. Tổng cộng 54/54 test bài chính và 3/3 test bonus đều qua.

**Kết quả:**
- Chạy riêng A1 3 lần: 95, 91, 97. Cả 3 lần không còn câu khẳng định "không có incident khác" và đều nêu số incident khớp.
- Chạy đủ 22 case: **97,32** (v2: 96,38 ± 0,58 qua 3 lần). Ba case E1, A6, H5 thấp hơn mức thấp nhất của v2 khoảng 1–6 điểm, nhưng lý do judge đưa ra đều tích cực; có vẻ là dao động của judge chứ không phải lỗi mới.
- Lỗi cũ chỉ xuất hiện 1/9 lần chạy, nên eval khó chứng minh lỗi đã hết. Bằng chứng chính cho cơ chế này là test offline, còn eval để xác nhận không làm hỏng case khác.

**Thiếu sót mới thấy ở A1 (84 điểm):** câu trả lời đã đúng về số lượng ("23 incident khớp, còn 18 chưa xem"), nhưng agent mở 4 incident GitHub mà **không mở incident Cloudflare**, nên thiếu URL Cloudflare dù câu hỏi yêu cầu URL cho cả hai. Kiểm tra nguồn hiện chỉ đòi *ít nhất một* URL, nên không bắt được lỗi này. Hướng sửa tiếp: kiểm tra nguồn theo từng nhà cung cấp được hỏi. Chưa thể kết luận ghi chú mới có góp phần gây ra lỗi này (khiến model mở thêm incident GitHub) hay không.

Chi phí vòng này: khoảng 0,03 USD.
