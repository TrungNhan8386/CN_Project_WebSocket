from random import randint
import sys, traceback, threading, socket

from VideoStream import VideoStream
from RtpPacket import RtpPacket

class ServerWorker:
	SETUP = 'SETUP'
	PLAY = 'PLAY'
	PAUSE = 'PAUSE'
	TEARDOWN = 'TEARDOWN'
	
	INIT = 0
	READY = 1
	PLAYING = 2
	state = INIT

	OK_200 = 0
	FILE_NOT_FOUND_404 = 1
	CON_ERR_500 = 2
	
	clientInfo = {}
	
	def __init__(self, clientInfo):
		self.clientInfo = clientInfo
		
	def run(self):
		threading.Thread(target=self.recvRtspRequest).start()
	
	def recvRtspRequest(self):
			"""Receive RTSP request from the client."""
			connSocket = self.clientInfo['rtspSocket'][0]
			while True:            
				try:
					data = connSocket.recv(256)
					if data:
						print("Data received:\n" + data.decode("utf-8"))
						self.processRtspRequest(data.decode("utf-8"))
					else:
						# Client đóng kết nối (gửi FIN packet)
						break
				except Exception:
					# Client đóng kết nối đột ngột (TEARDOWN hoặc tắt app)
					# Đây là nơi bắt lỗi WinError 10054
					print("Client finished connection.")
					break
	
	def processRtspRequest(self, data):
		"""Process RTSP request sent from the client."""
		# Get the request type
		request = data.split('\n')
		line1 = request[0].split(' ')
		requestType = line1[0]
		
		# Get the media file name
		filename = line1[1]
		
		# Get the RTSP sequence number 
		seq = request[1].split(' ')
		
		# Process SETUP request
		if requestType == self.SETUP:
			if self.state == self.INIT:
				# Update state
				print("processing SETUP\n")
				
				try:
					self.clientInfo['videoStream'] = VideoStream(filename)
					self.state = self.READY
				except IOError:
					self.replyRtsp(self.FILE_NOT_FOUND_404, seq[1])
				
				# Generate a randomized RTSP session ID
				self.clientInfo['session'] = randint(100000, 999999)
				
				# Send RTSP reply
				self.replyRtsp(self.OK_200, seq[1])
				
				# Get the RTP/UDP port from the last line
				self.clientInfo['rtpPort'] = request[2].split(' ')[3]
		
		# Process PLAY request 		
		elif requestType == self.PLAY:
			if self.state == self.READY:
				print("processing PLAY\n")
				self.state = self.PLAYING
				
				# Create a new socket for RTP/UDP
				self.clientInfo["rtpSocket"] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
				
				self.replyRtsp(self.OK_200, seq[1])
				
				# Create a new thread and start sending RTP packets
				self.clientInfo['event'] = threading.Event()
				self.clientInfo['worker']= threading.Thread(target=self.sendRtp) 
				self.clientInfo['worker'].start()
		
		# Process PAUSE request
		elif requestType == self.PAUSE:
			if self.state == self.PLAYING:
				print("processing PAUSE\n")
				self.state = self.READY
				
				self.clientInfo['event'].set()
			
				self.replyRtsp(self.OK_200, seq[1])
		
		# Process TEARDOWN request
		elif requestType == self.TEARDOWN:
			print("processing TEARDOWN\n")

			self.clientInfo['event'].set()
			
			self.replyRtsp(self.OK_200, seq[1])
			
			# Close the RTP socket
			self.clientInfo['rtpSocket'].close()
			
	def sendRtp(self):
			"""Send RTP packets over UDP."""
			while True:
				self.clientInfo['event'].wait(0.05) 
				
				if self.clientInfo['event'].isSet(): 
					break 
					
				data = self.clientInfo['videoStream'].nextFrame()
				
				if data: 
					frameNumber = self.clientInfo['videoStream'].frameNbr()
					try:
						address = self.clientInfo['rtspSocket'][1][0]
						port = int(self.clientInfo['rtpPort'])
						
						# --- XỬ LÝ PHÂN MẢNH (FRAGMENTATION) ---
						# Nếu frame > 1400 bytes (MTU an toàn), cắt nhỏ ra
						MAX_PAYLOAD_SIZE = 1400
						data_len = len(data)
						
						if data_len > MAX_PAYLOAD_SIZE:
							# Cần chia nhỏ
							start = 0
							while start < data_len:
								end = start + MAX_PAYLOAD_SIZE
								if end >= data_len:
									end = data_len
									marker = 1 # Mảnh cuối cùng của frame -> Marker = 1
								else:
									marker = 0 # Chưa hết frame -> Marker = 0
								
								payload = data[start:end]
								self.clientInfo['rtpSocket'].sendto(
									self.makeRtp(payload, frameNumber, marker), 
									(address, port)
								)
								start = end
						else:
							# Frame nhỏ, gửi 1 gói như cũ, Marker luôn là 1
							self.clientInfo['rtpSocket'].sendto(
								self.makeRtp(data, frameNumber, 1), 
								(address, port)
							)
							
					except Exception as e:
						print("Connection Error or Send Error")
						# traceback.print_exc()

	def makeRtp(self, payload, frameNbr, marker_bit=0):
			"""RTP-packetize the video data."""
			version = 2
			padding = 0
			extension = 0
			cc = 0
			marker = marker_bit # Dùng tham số truyền vào
			pt = 26 
			seqnum = frameNbr
			ssrc = 0 
			
			rtpPacket = RtpPacket()
			rtpPacket.encode(version, padding, extension, cc, seqnum, marker, pt, ssrc, payload)
			
			return rtpPacket.getPacket()
		
	def replyRtsp(self, code, seq):
		"""Send RTSP reply to the client."""
		if code == self.OK_200:
			#print("200 OK")
			reply = 'RTSP/1.0 200 OK\nCSeq: ' + seq + '\nSession: ' + str(self.clientInfo['session'])
			connSocket = self.clientInfo['rtspSocket'][0]
			connSocket.send(reply.encode())
		
		# Error messages
		elif code == self.FILE_NOT_FOUND_404:
			print("404 NOT FOUND")
		elif code == self.CON_ERR_500:
			print("500 CONNECTION ERROR")
