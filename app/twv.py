import threading

from pims import FramesSequence, Frame
from ctypes import *
import numpy as np


class TArFrameROI(Structure):
    _pack_ = 1
    _fields_ = [('Left', c_uint16),
                ('Top', c_uint16),
                ('Width', c_uint16),
                ('Height', c_uint16)]


class TArFrameData(Structure):
    _pack_ = 1
    _fields_ = [('HeaderSize', c_uint16),
                ('FrameDataIncl', c_uint16),
                ('ROI', TArFrameROI),
                ('BytesPerPixel', c_uint16),
                ('FrameRate', c_double),
                ('Exposure', c_double),
                ('Gain', c_double),
                ('RecTrapDataNo', c_uint16)]


class TArCalibrationData(Structure):
    _pack_ = 1
    _fields_ = [('ImageToSampleScale', c_float)]


class TArVideoHeader(Structure):
    _pack_ = 1
    _fields_ = [('VideoID', c_uint16),
                ('VideoVersion', c_uint16),
                ('VideoLicence', c_uint16),
                ('VideoHeaderSize', c_uint32),
                ('RecordedFrames', c_uint32),
                ('FrameData', TArFrameData),
                ('CalibrationData', TArCalibrationData)]


class TArTrapData(Structure):
    _pack_ = 1
    _fields_ = [('PositionX', c_float),
                ('PositionY', c_float),
                ('Intensity', c_float)]


class TArFrameHeader(Structure):
    _pack_ = 1
    _fields_ = [('FrameNumber', c_uint32),
                ('FrameTime', c_float),
                ('LaserPower', c_float),
                ('CalibrationData1', TArTrapData),
                ('CalibrationData2', TArTrapData),
                ('CalibrationData3', TArTrapData),
                ('CalibrationData4', TArTrapData)]


class TWVReader(FramesSequence):
    """PIMS reader for TWV video files."""

    def __init__(self, filename):
        self.filename = filename
        
        # Open file and read video header
        self._f = open(filename, 'rb')
        self._lock = threading.Lock()
        self.videoHeader = TArVideoHeader()

        # Read video header
        self._f.readinto(self.videoHeader)
        self.videoHeaderSize = getattr(self.videoHeader, 'VideoHeaderSize')
        self._len = getattr(self.videoHeader, 'RecordedFrames')
        
        # Get frame data and ROI information
        self.frameData = getattr(self.videoHeader, 'FrameData')
        self.frameROI = getattr(self.frameData, 'ROI')
        
        # Save important data from headers
        self.frameHeaderSize = getattr(self.frameData, 'HeaderSize')
        self.imageW = getattr(self.frameROI, 'Width')
        self.imageH = getattr(self.frameROI, 'Height')
        self.imageSize = self.imageW * self.imageH
        
        # Set frame properties
        self._frame_shape = (self.imageH, self.imageW)
        self._dtype = np.uint8

    def _frame_position(self, num):
        """Calculate the beginning of desired frame in file."""
        if isinstance(num, int):
            if num < 0 or num >= self._len:
                raise IndexError('Frame index out of range')
            else:
                return self.videoHeaderSize + num * (self.frameHeaderSize + self.imageSize)
        else:
            raise ValueError('Frame number must be an integer')

    def _read_header(self, i):
        """Seek to frame i and read its header. Caller must hold self._lock."""
        frameHeader = TArFrameHeader()
        self._f.seek(self._frame_position(i))
        self._f.readinto(frameHeader)
        return frameHeader

    def get_timestamp(self, i):
        """Return the recorded FrameTime for frame i, without reading its image data."""
        with self._lock:
            frameHeader = self._read_header(i)
        return getattr(frameHeader, 'FrameTime')

    def get_frame(self, i):
        """Get frame at index i."""
        with self._lock:
            frameHeader = self._read_header(i)

            # Read image data
            image = np.fromfile(self._f, np.uint8, self.imageSize).reshape(self.imageH, self.imageW)

        # Get timestamp from frame header
        timestamp = getattr(frameHeader, 'FrameTime')

        # Create Frame with metadata
        frame = Frame(image, frame_no=i)
        frame.metadata = {'timestamp': timestamp}

        return frame

    def __len__(self):
        return self._len

    @property
    def frame_shape(self):
        return self._frame_shape
    
    @property
    def pixel_type(self):
        return self._dtype
    
    def close(self):
        """Close the file."""
        if hasattr(self, '_f') and self._f:
            self._f.close()
            
    def __del__(self):
        """Destructor to ensure file is closed."""
        self.close()