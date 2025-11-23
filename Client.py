from tkinter import *
import tkinter.messagebox as tkMessageBox
from PIL import Image, ImageTk
import socket, threading, sys, traceback, os

# ĐẢM BẢO BẠN CÓ FILE NÀY
try:
    from RtpPacket import RtpPacket
except ImportError:
    print("\n[Lỗi] Không tìm thấy file 'RtpPacket.py'. Vui lòng để file RtpPacket.py cùng thư mục với Client.py\n")
    sys.exit(1)
    
import queue

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"

class Client:
    INIT = 0
    READY = 1
    PLAYING = 2
    state = INIT
    
    SETUP = 0
    PLAY = 1
    PAUSE = 2
    TEARDOWN = 3
    
    # Initiation..
    def __init__(self, master, serveraddr, serverport, rtpport, filename):
        self.master = master
        self.master.protocol("WM_DELETE_WINDOW", self.handler)
        self.createWidgets()
        self.serverAddr = serveraddr
        self.serverPort = int(serverport)
        self.rtpPort = int(rtpport)
        self.fileName = filename
        self.rtspSeq = 0
        self.sessionId = 0
        self.requestSent = -1
        self.teardownAcked = 0
        self.connectToServer()
        self.frameNbr = 0
        #---
        # --- THỐNG KÊ DATA ANALYSIS ---
        self.total_packets_received = 0
        self.total_packets_lost = 0
        self.total_bytes_received = 0
        self.start_time = 0
        self.packet_loss_rate = 0.0
        self.data_rate = 0.0 # kbps
        self.temp_frame_buffer = b'' # Buffer tạm để gom mảnh
        #---
        self.bytes_recv = 0
        self.play_start_ts = None
        #---
        self.frame_buffer =queue.Queue()
        self.BUFFER_THREHOLD = 30 # Đệm 200 frame để chạy mượt
        self.is_buffering = True
        
    def createWidgets(self):
        """Build GUI."""
        # Create Setup button
        self.setup = Button(self.master, width=20, padx=3, pady=3)
        self.setup["text"] = "Setup"
        self.setup["command"] = self.setupMovie
        self.setup.grid(row=1, column=0, padx=2, pady=2)
        
        # Create Play button         
        self.start = Button(self.master, width=20, padx=3, pady=3)
        self.start["text"] = "Play"
        self.start["command"] = self.playMovie
        self.start.grid(row=1, column=1, padx=2, pady=2)
        
        # Create Pause button         
        self.pause = Button(self.master, width=20, padx=3, pady=3)
        self.pause["text"] = "Pause"
        self.pause["command"] = self.pauseMovie
        self.pause.grid(row=1, column=2, padx=2, pady=2)
        
        # Create Teardown button
        self.teardown = Button(self.master, width=20, padx=3, pady=3)
        self.teardown["text"] = "Teardown"
        self.teardown["command"] =  self.exitClient
        self.teardown.grid(row=1, column=3, padx=2, pady=2)
        
        # Create a label to display the movie
        self.label = Label(self.master, height=19)
        self.label.grid(row=0, column=0, columnspan=4, sticky=W+E+N+S, padx=5, pady=5) 
    
    def setupMovie(self):
        """Setup button handler."""
        if self.state == self.INIT:
            self.sendRtspRequest(self.SETUP)
    
    def exitClient(self):
        """Teardown button handler."""
        # Dừng mọi thứ trước khi thoát
        try:
            # Chỉ dừng nếu đang PLAYING
            if self.state == self.PLAYING:
                self.sendRtspRequest(self.PAUSE)
                if hasattr(self, 'playEvent'):
                    self.playEvent.set()
                try:
                    self.rtpSocket.shutdown(socket.SHUT_RDWR)
                    self.rtpSocket.close()
                except Exception:
                    pass
        except Exception:
            pass # Bỏ qua nếu đã dừng
            
        self.sendRtspRequest(self.TEARDOWN)    
        try:
            os.remove(CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT) # Delete the cache image from video
        except Exception:
            pass
        self.master.destroy() # Close the gui window
    
    def pauseMovie(self):
        """Pause button handler."""
        if self.state == self.PLAYING:
            self.sendRtspRequest(self.PAUSE)
            
            # 1. Đặt cờ dừng luồng listenRtp
            if hasattr(self, 'playEvent'):
                self.playEvent.set()
            
            # 2. Xóa buffer
            with self.frame_buffer.mutex:
                self.frame_buffer.queue.clear()
            self.state = self.READY

            # 3. Do NOT close RTP socket here. Keep the UDP port bound so it can
            #    be reused immediately when resuming playback. The listener
            #    thread will exit because we set `playEvent` above.
    
    def playMovie(self):
        """Play button handler."""
        # Handle INIT state (first play)
        if self.state == self.INIT:
            print("State is INIT. Sending SETUP request...")
            # Send SETUP request to initialize the movie
            self.sendRtspRequest(self.SETUP)
            print("SETUP request sent. Waiting for server response...")
            self.state = self.READY  # Transition to READY state
            print("State changed to READY.")

        # Handle READY state (play the movie)
        if self.state == self.READY:
            print("State is READY. Preparing to play...")
            # Change state to PLAYING immediately to prevent double-clicks
            self.state = self.PLAYING 
            
            # Open RTP port if needed
            if not hasattr(self, 'rtpSocket') or self.rtpSocket.fileno() == -1:
                print("RTP socket not open. Attempting to open RTP port...")
                if not self.openRtpPort():
                    print("Failed to open RTP port.")
                    self.state = self.READY  # Revert state if RTP port fails
                    return  # Exit without doing anything
                print("RTP port opened successfully.")
        
        # Prepare/clear the playEvent before starting the listener thread
        if hasattr(self, 'playEvent'):
            # If previously set (paused), clear it to resume
            try:
                if self.playEvent.isSet():
                    print("Clearing existing playEvent to resume playback...")
                    self.playEvent.clear()
            except Exception:
                # If playEvent is not usable, recreate
                self.playEvent = threading.Event()
                self.playEvent.clear()
        else:
            self.playEvent = threading.Event()
            self.playEvent.clear()

        # Create a new thread to listen for RTP packets
        print("Starting RTP listening thread...")
        threading.Thread(target=self.listenRtp, daemon=True).start()
        
        # Send PLAY request
        print("Sending PLAY request...")
        self.sendRtspRequest(self.PLAY)
        print("PLAY request sent.")

        # Reset buffer state
        print("Resetting buffer state...")
        self.is_buffering = True
        with self.frame_buffer.mutex:
            self.frame_buffer.queue.clear()
        print("Playback started.")
    
    def play_buffered_video(self):
        """Vòng lặp chính hiển thị video (40ms ~ 25fps)"""
        # Kiểm tra nếu đã teardown thì dừng vòng lặp
        if self.teardownAcked: 
            return
        
        # (Giữ state READY để buffer có thể được làm đầy trước)
        if self.state != self.PLAYING and self.state != self.READY:
            self.master.after(40, self.play_buffered_video) # Lặp lại sau
            return

        # LOGIC BUFFERING
        if self.is_buffering:
            if self.frame_buffer.qsize() >= self.BUFFER_THREHOLD:
                print(f"[Buffering] Complete! Buffer size: {self.frame_buffer.qsize()}")
                self.is_buffering = False
            else:
                pass # Vẫn đang đợi buffer đầy

        # LOGIC HIỂN THỊ (Chỉ hiển thị khi state thực sự là PLAYING)
        if self.state == self.PLAYING and not self.is_buffering and not self.frame_buffer.empty():
            try:
                frame_data = self.frame_buffer.get_nowait()
                image_path = self.writeFrame(frame_data)
                self.updateMovie(image_path)
            except queue.Empty:
                pass # Không có frame, không làm gì
    
        # Lên lịch cho lần chạy tiếp theo
        self.master.after(40, self.play_buffered_video)

    def listenRtp(self):        
            """Listen for RTP packets."""
            import time
            self.start_time = time.time()
            
            expected_seq_num = 0 # Dùng để tính packet loss

            while True:
                # 1. Check stop flag
                if hasattr(self, 'playEvent') and self.playEvent.isSet():
                    break

                try:
                    # 2. Receive data
                    data = self.rtpSocket.recv(20480) 

                    if hasattr(self, 'playEvent') and self.playEvent.isSet():
                        continue

                    if data:
                        rtpPacket = RtpPacket()
                        rtpPacket.decode(data)
                        
                        # --- THỐNG KÊ (ANALYSIS) ---
                        currSeqNum = rtpPacket.seqNum()
                        self.total_packets_received += 1
                        self.total_bytes_received += len(rtpPacket.getPayload())
                        
                        # Tính Packet Loss cơ bản
                        if currSeqNum > expected_seq_num + 1:
                            # Nếu nhảy cóc số thứ tự -> mất gói
                            loss = currSeqNum - (expected_seq_num + 1)
                            self.total_packets_lost += loss
                            # print(f"Lost {loss} packets!")
                        
                        expected_seq_num = currSeqNum

                        # Cập nhật tỉ lệ (có thể in ra console hoặc hiện lên GUI)
                        elapsed = time.time() - self.start_time
                        if elapsed > 0:
                            self.data_rate = (self.total_bytes_received * 8) / (elapsed * 1000) # kbps
                        if (self.total_packets_received + self.total_packets_lost) > 0:
                            self.packet_loss_rate = (self.total_packets_lost / (self.total_packets_received + self.total_packets_lost)) * 100
                        
                        # In thống kê mỗi 100 gói nhận được (để đỡ spam log)
                        if self.total_packets_received % 100 == 0:
                            print(f"[Stats] Rate: {self.data_rate:.2f} kbps | Loss: {self.packet_loss_rate:.2f}%")

                        # --- REASSEMBLY (GOM MẢNH) ---
                        marker = rtpPacket.getMarker()
                        payload = rtpPacket.getPayload()
                        
                        # Gom payload vào buffer tạm
                        self.temp_frame_buffer += payload
                        
                        # Nếu Marker = 1 -> Đã nhận đủ một Frame hoàn chỉnh
                        if marker == 1:
                            # Chỉ xử lý khi đủ frame
                            # Logic cũ: so sánh seqNum để tránh duplicate frame
                            if currSeqNum > self.frameNbr: 
                                self.frameNbr = currSeqNum
                                if not (hasattr(self, 'playEvent') and self.playEvent.isSet()):
                                    # Đẩy toàn bộ frame đã ghép vào buffer hiển thị
                                    self.frame_buffer.put(self.temp_frame_buffer)
                            
                            # Reset buffer tạm
                            self.temp_frame_buffer = b''
                
                except socket.timeout:
                    continue
                except Exception as e:
                    # print(f"RTP Exception: {e}")
                    break
        
            # Dọn dẹp socket khi TEARDOWN
            if self.teardownAcked == 1:
                try:
                    self.rtpSocket.shutdown(socket.SHUT_RDWR)
                    self.rtpSocket.close()
                except Exception:
                    pass
            
    def writeFrame(self, data):
        """Write the received frame to a temp image file. Return the image file."""
        cachename = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
        try:
            file = open(cachename, "wb")
            file.write(data)
            file.close()
        except Exception as e:
            print(f"Error writing frame: {e}")
            return "" 
        return cachename
    
    def updateMovie(self, imageFile):
            """Update the image file as video frame in the GUI."""
            if not imageFile: 
                return
            try:
                photo = ImageTk.PhotoImage(Image.open(imageFile))
                
                # --- CODE THÔNG MINH ---
                # Nếu ảnh nhỏ (như movie.Mjpeg), giữ height=288 cho giống hệt code gốc
                if photo.height() <= 288:
                    self.label.configure(image = photo, height=288)
                # Nếu ảnh lớn (HD), thì bỏ ép buộc chiều cao để nó tự bung ra
                else:
                    self.label.configure(image = photo, height=photo.height())
                # -----------------------
                
                self.label.image = photo
            except Exception as e:
                pass

    def connectToServer(self):
        """Connect to the Server. Start a new RTSP/TCP session."""
        self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.rtspSocket.connect((self.serverAddr, self.serverPort))
        except:
            tkMessageBox.showwarning('Connection Failed', 'Connection to \'%s\' failed.' % self.serverAddr)
        
    def sendRtspRequest(self, requestCode):
        """Send RTSP request to the server."""    
        self.rtspSeq += 1

        # Setup request
        if requestCode == self.SETUP and self.state == self.INIT:
            threading.Thread(target=self.recvRtspReply, daemon=True).start()
            request = f"SETUP {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nTransport: RTP/UDP; client_port= {self.rtpPort}"
            self.requestSent = self.SETUP

        # Play request
        # (LƯU Ý: state đã được đổi thành PLAYING ở playMovie)
        elif requestCode == self.PLAY and self.state == self.PLAYING:
            request = f"PLAY {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
            self.requestSent = self.PLAY

        # Pause request
        elif requestCode == self.PAUSE and (self.state == self.PLAYING or self.state == self.READY):
            request = f"PAUSE {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
            self.requestSent = self.PAUSE

        # Teardown request
        elif requestCode == self.TEARDOWN and not self.state == self.INIT:
            request = f"TEARDOWN {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
            self.requestSent = self.TEARDOWN
        else:
            return
        
        # Send the RTSP request using rtspSocket.
        try:
            self.rtspSocket.send(request.encode())
            print('\nData sent:\n' + request)
        except Exception as e:
            print(f"Error sending RTSP request: {e}")
            if self.state != self.INIT: # Không hiển thị lỗi nếu đang teardown
                tkMessageBox.showwarning('Connection Error', 'Failed to send request. Is server down?')

    
    def recvRtspReply(self):
        """Receive RTSP reply from the server."""
        while True:
            try:
                reply = self.rtspSocket.recv(1024)
                if not reply:
                    break
                self.parseRtspReply(reply.decode("utf-8"))
                if self.requestSent == self.TEARDOWN:
                    break
            except Exception as e:
                break # Socket đã đóng
        
        try:
            self.rtspSocket.shutdown(socket.SHUT_RDWR)
            self.rtspSocket.close()
        except Exception:
            pass

    # === HÀM ĐÃ SỬA LỖI CUỐI CÙNG ===
    def parseRtspReply(self, data):
        """Parse the RTSP reply from the server."""
        lines = data.split('\n')
        if len(lines) < 2: return
        try:
            seqNum = int(lines[1].split(' ')[1])
            # Minimal debug: clearly label RTSP CSeq
            print(f"RTSP-CSeq: {seqNum}")
        except (IndexError, ValueError):
            return
        
        if seqNum == self.rtspSeq:
            if len(lines) < 3: return
            
            try:
                session = int(lines[2].split(' ')[1])
            except (IndexError, ValueError): return
                
            if self.sessionId == 0:
                self.sessionId = session
            
            if self.sessionId == session:
                # Xử lý lỗi từ server (ví dụ: file not found)
                statusCode = int(lines[0].split(' ')[1])
                if statusCode != 200:
                    print(f"Server returned error: {lines[0]}")
                    
                    # === BẮT ĐẦU SỬA LỖI ===
                    # Nếu server từ chối PLAY, chúng ta phải dọn dẹp
                    if self.requestSent == self.PLAY:
                        # 1. Tắt luồng "mồ côi"
                        if hasattr(self, 'playEvent'):
                            self.playEvent.set()
                        # 2. Đóng socket mà nó đang giữ
                        try:
                            self.rtpSocket.shutdown(socket.SHUT_RDWR)
                            self.rtpSocket.close()
                        except Exception:
                            pass
                        # 3. Khôi phục state về READY
                        self.state = self.READY 
                    # === KẾT THÚC SỬA LỖI ===
                        
                    elif self.requestSent == self.SETUP:
                        # Lỗi nghiêm trọng, không thể setup
                        tkMessageBox.showerror("Setup Failed", f"Server returned error:\n{lines[0]}")
                        self.master.destroy()
                    return

                # Xử lý khi server trả lời OK (200)
                if self.requestSent == self.SETUP:
                    self.state = self.READY
                    # Mở RTP port LẦN ĐẦU TIÊN
                    if self.openRtpPort(): 
                        # Bắt đầu vòng lặp hiển thị MỘT LẦN DUY NHẤT
                        self.play_buffered_video()
            
                elif self.requestSent == self.PLAY:
                    # Server chấp nhận PLAY.
                    # (state đã được set là PLAYING ở playMovie)
                    import time
                    self.play_start_ts = time.time()
                
                elif self.requestSent == self.PAUSE:
                    # (Đã sửa lỗi) Không làm gì ở đây nữa
                    pass
                        
                elif self.requestSent == self.TEARDOWN:
                    self.state = self.INIT
                    self.teardownAcked = 1 
    
    def openRtpPort(self):
        """Open RTP socket binded to a specified port.
           Returns True on success, False on failure."""
        try:
            self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            
            # GIẢI QUYẾT LỖI "ADDRESS ALREADY IN USE"
            self.rtpSocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            
            self.rtpSocket.settimeout(0.5)
            self.rtpSocket.bind(('', self.rtpPort))
            print(f"openRtpPort: bound UDP port {self.rtpPort}")
            return True # Mở port thành công
        except Exception as e:
            print(f"openRtpPort: failed to bind UDP port {self.rtpPort}: {e}")
            tkMessageBox.showwarning('Unable to Bind', 'Unable to bind PORT=%d\nError: %s' % (self.rtpPort, e))
            return False # Mở port thất bại

    def handler(self):
        """Handler on explicitly closing the GUI window."""
        
        # Cờ này theo dõi xem CHÚNG TA có tự động pause hay không
        was_playing = (self.state == self.PLAYING)
        
        if was_playing:
            self.pauseMovie() # Tạm dừng ngay lập tức
            
        if tkMessageBox.askokcancel("Quit?", "Are you sure you want to quit?"):
            self.exitClient()
        else: # Khi nhấn "Cancel"
            # Nếu TRƯƯỚC ĐÓ nó đang chạy, thì play lại
            if was_playing: 
                 self.playMovie()