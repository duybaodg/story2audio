# Hướng dẫn Đóng góp (Contributing Guidelines)

Cảm ơn bạn đã quan tâm và muốn đóng góp cho dự án **Ebook2Audio**! Chúng tôi hoan nghênh mọi đóng góp từ cộng đồng, từ việc báo lỗi (bug report), đề xuất tính năng mới (feature request) cho đến việc gửi Pull Request (PR) để cải thiện mã nguồn.

Dưới đây là một số hướng dẫn để quá trình đóng góp diễn ra suôn sẻ và hiệu quả.

---

## 🐞 Báo cáo Lỗi (Reporting Bugs)

Nếu bạn phát hiện lỗi trong quá trình sử dụng, vui lòng kiểm tra mục [Issues](https://github.com/dvchd/ebook2audio/issues) xem lỗi này đã được báo cáo chưa. Nếu chưa, hãy tạo một Issue mới với các thông tin sau:

1. **Mô tả lỗi:** Lỗi gì đang xảy ra? (càng chi tiết càng tốt).
2. **Cách tái hiện (Steps to reproduce):** Các bước cụ thể để chúng tôi có thể gặp lỗi giống như bạn.
3. **Kết quả mong đợi (Expected behavior):** Bạn mong muốn hệ thống hoạt động như thế nào trong trường hợp đó.
4. **Môi trường (Environment):** Hệ điều hành, trình duyệt, phiên bản Python, Docker (nếu có),...
5. **Log/Ảnh chụp màn hình (nếu có):** Bất kỳ thông tin nào giúp ích cho việc debug.

## 💡 Đề xuất Tính năng (Suggesting Enhancements)

Chúng tôi luôn muốn làm cho Ebook2Audio tốt hơn. Để đề xuất tính năng mới, hãy tạo một Issue và mô tả:

1. **Vấn đề bạn đang gặp phải:** Tại sao tính năng này lại cần thiết?
2. **Giải pháp đề xuất:** Bạn muốn tính năng hoạt động như thế nào?
3. **Giải pháp thay thế (Alternatives):** Bạn đã thử cách nào khác chưa?

## 💻 Môi trường Phát triển (Development Setup)

Để bắt đầu đóng góp mã nguồn, bạn cần thiết lập môi trường phát triển:

1. **Fork repository** này về tài khoản GitHub của bạn.
2. **Clone fork** của bạn về máy:
   ```bash
   git clone https://github.com/YOUR-USERNAME/ebook2audio.git
   cd ebook2audio
   ```
3. **Cài đặt dependencies** (khuyến nghị dùng `uv`):
   ```bash
   uv sync
   ```
   *Hoặc dùng `pip`:*
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install fastapi edge-tts gtts python-dotenv "uvicorn[standard]"
   ```
4. **Tạo branch mới** cho tính năng/bản vá của bạn:
   ```bash
   git checkout -b feature/ten-tinh-nang
   # hoặc
   git checkout -b fix/ten-loi
   ```

## 🛠 Quy trình Gửi Pull Request (Pull Request Process)

1. **Đảm bảo code hoạt động tốt:** Chạy thử ứng dụng local và đảm bảo tính năng mới hoạt động, không làm hỏng các tính năng cũ.
2. **Tuân thủ Coding Style:** Giữ phong cách code nhất quán với dự án (FastAPI, Javascript thuần).
3. **Commit rõ ràng:** Viết commit message mô tả rõ những thay đổi bạn đã làm (tham khảo [Conventional Commits](https://www.conventionalcommits.org/)).
   *Ví dụ: `feat: thêm hỗ trợ ngôn ngữ Tây Ban Nha` hoặc `fix: sửa lỗi mất cache khi tải trang`.*
4. **Cập nhật tài liệu (nếu cần):** Nếu tính năng của bạn yêu cầu cài đặt mới hoặc thay đổi cách sử dụng, hãy cập nhật `README.md`.
5. **Push branch và tạo PR:**
   ```bash
   git push origin branch-cua-ban
   ```
   Sau đó lên GitHub và tạo Pull Request vào nhánh `main` của repository gốc.
6. **Code Review:** Đội ngũ maintainer (hoặc AI bot như CodeRabbit) sẽ review code của bạn. Hãy sẵn sàng thảo luận và chỉnh sửa nếu có yêu cầu.

---

Cảm ơn bạn đã dành thời gian và công sức để cải thiện Ebook2Audio! 🚀
