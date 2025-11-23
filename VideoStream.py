import os

class VideoStream:
    def __init__(self, filename):
        self.filename = filename
        try:
            self.file = open(filename, 'rb')
        except:
            raise IOError
        self.frameNum = 0
        
        # Kiểm tra xem có phải file đề bài cũ (movie.Mjpeg) hay không
        # Logic: File đề bài dùng header độ dài 5 byte. File HD chuẩn dùng Marker JPEG.
        self.is_proprietary = False
        if "movie.Mjpeg" in filename:
            self.is_proprietary = True

    def nextFrame(self):
        """Get next frame."""
        data = None
        
        if self.is_proprietary:
            # --- CÁCH XỬ LÝ CŨ CHO movie.Mjpeg (GIỮ NGUYÊN CHO NO.1) ---
            length_str = self.file.read(5) # Get the framelength from the first 5 bits
            if length_str: 
                try:
                    framelength = int(length_str)
                    data = self.file.read(framelength)
                    self.frameNum += 1
                except ValueError:
                    pass
        else:
            # --- CÁCH XỬ LÝ MỚI CHO HD VIDEO (STANDARD MJPEG) ---
            # Tìm Start of Image (SOI): 0xFF 0xD8
            while True:
                byte = self.file.read(1)
                if not byte: return None # End of file
                if byte == b'\xff':
                    next_byte = self.file.read(1)
                    if next_byte == b'\xd8':
                        # Tìm thấy bắt đầu frame
                        frame_data = b'\xff\xd8'
                        break
            
            # Đọc tiếp cho đến khi gặp End of Image (EOI): 0xFF 0xD9
            while True:
                byte = self.file.read(1)
                if not byte: return None
                frame_data += byte
                if byte == b'\xff':
                    next_byte = self.file.read(1)
                    frame_data += next_byte
                    if next_byte == b'\xd9':
                        # Tìm thấy kết thúc frame
                        data = frame_data
                        self.frameNum += 1
                        break
                        
        return data
        
    def frameNbr(self):
        """Get frame number."""
        return self.frameNum