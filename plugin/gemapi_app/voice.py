"""Qt5 microphone, optional speech and Voxtral transport; audio stays in memory."""

from array import array
import base64
import binascii
from io import BytesIO
import json
import sys
from uuid import uuid4
import wave

from qgis.PyQt import sip
from qgis.PyQt.QtCore import (QBuffer, QElapsedTimer, QIODevice, QObject,
                             QThread, QTimer, Qt, QUrl, pyqtSignal, pyqtSlot)
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest

TRANSCRIPTION_ENDPOINT = "https://api.mistral.ai/v1/audio/transcriptions"
TRANSCRIPTION_MODEL = "voxtral-mini-transcribe-3"
MAX_SECONDS = 60
MAX_AUDIO_BYTES = 48000 * 2 * 2 * MAX_SECONDS
# Retain threads if a dialog is unloaded during native device initialization.
# No QThread is destroyed while its native audio backend is still running.
_CAPTURES = set()
SPEECH_ENDPOINT = "https://api.mistral.ai/v1/audio/speech"
SPEECH_MODEL = "voxtral-mini-tts-3-0"
SPEECH_VOICE = "voice_592"  # Marie, French preset; never clone a user's voice.
SPEECH_SETTING = "gemapi_app/spoken_responses"
MAX_SPEECH_TEXT = 360
MAX_SPEECH_BYTES = 2 * 1024 * 1024
MAX_SPEECH_RESPONSE = 3 * 1024 * 1024
SPEECH_TIMEOUT_MS = 30000
_PLAYBACKS = set()


class _AudioThread(QThread):
    @pyqtSlot()
    def interrupt(self):
        # Unlike requestInterruption(), a Qt slot safely discards destroyed(QObject*)'s argument.
        self.requestInterruption()


def speech_payload(text):
    if (not isinstance(text, str) or not text.strip() or len(text) > MAX_SPEECH_TEXT or
            any(ord(char) < 32 for char in text)):
        raise ValueError("La réponse vocale doit être une phrase courte.")
    return {"model": SPEECH_MODEL, "input": text.strip(), "voice_id": SPEECH_VOICE,
            "response_format": "wav"}


def parse_speech(raw):
    """Validate bounded base64 + WAV, returning only PCM samples and their format."""
    try:
        if not raw or len(raw) > MAX_SPEECH_RESPONSE:
            raise ValueError()
        encoded = json.loads(raw)["audio_data"]
        if not isinstance(encoded, str) or len(encoded) > (MAX_SPEECH_BYTES + 2) // 3 * 4:
            raise ValueError()
        audio = base64.b64decode(encoded, validate=True)
        if len(audio) > MAX_SPEECH_BYTES or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise ValueError()
        if int.from_bytes(audio[4:8], 'little') + 8 != len(audio):
            raise ValueError()
        with wave.open(BytesIO(audio), 'rb') as wav:
            rate, channels, frames = wav.getframerate(), wav.getnchannels(), wav.getnframes()
            if (wav.getcomptype() != 'NONE' or wav.getsampwidth() != 2 or channels not in (1, 2) or
                    not 8000 <= rate <= 48000 or not 0 < frames <= rate * 30):
                raise ValueError()
            pcm = wav.readframes(frames)
            if len(pcm) != frames * channels * 2:
                raise ValueError()
        return pcm, rate, channels
    except (ValueError, TypeError, KeyError, binascii.Error, wave.Error, EOFError, RecursionError):
        raise ValueError("Audio de réponse invalide ou trop volumineux. Le texte reste disponible.") from None


class _PlaybackWorker(QObject):
    status = pyqtSignal(int, str)
    failed = pyqtSignal(int, str)
    finished = pyqtSignal()
    finish_requested = pyqtSignal(str)

    def __init__(self, audio, generation=0):
        super().__init__()
        self.generation = generation
        self.pcm, self.rate, self.channels = audio
        self.output = self.buffer = self.timer = None
        self.done = False
        self.finish_requested.connect(self._finish, Qt.QueuedConnection)

    @pyqtSlot()
    def start(self):
        if QThread.currentThread().isInterruptionRequested():
            self.cancel()
            return
        try:
            from qgis.PyQt.QtMultimedia import QAudio, QAudioDeviceInfo, QAudioFormat, QAudioOutput
            device = QAudioDeviceInfo.defaultOutputDevice()
            if device.isNull():
                raise ValueError("Aucun périphérique de sortie audio disponible. Le texte reste disponible.")
            fmt = QAudioFormat()
            fmt.setCodec('audio/pcm')
            fmt.setSampleRate(self.rate)
            fmt.setChannelCount(self.channels)
            fmt.setSampleSize(16)
            fmt.setSampleType(QAudioFormat.SignedInt)
            fmt.setByteOrder(QAudioFormat.LittleEndian)
            if not device.isFormatSupported(fmt):
                raise ValueError("Le périphérique audio ne prend pas en charge cette réponse. Le texte reste disponible.")
            self.buffer = QBuffer(self)
            self.buffer.setData(self.pcm)
            self.pcm = b''
            self.buffer.open(QIODevice.ReadOnly)
            self.output = QAudioOutput(device, fmt, self)
            self.output.stateChanged.connect(self._state)
            self.timer = QTimer(self)
            self.timer.setSingleShot(True)
            self.timer.timeout.connect(self.cancel)
            self.timer.start(31000)
            # Device initialization may block its worker, never the QGIS GUI.
            if QThread.currentThread().isInterruptionRequested():
                self.cancel()
                return
            self.output.start(self.buffer)
            if not self.done and self.output.error() != QAudio.NoError:
                raise ValueError("Lecture audio indisponible. Le texte reste disponible.")
            if not self.done:
                self.status.emit(self.generation, "Lecture de la réponse…")
        except (ImportError, OSError, RuntimeError, ValueError):
            self._finish("Lecture vocale indisponible : vérifiez la sortie audio et QtMultimedia. Le texte reste disponible.")

    def _state(self, state):
        if self.done:
            return
        from qgis.PyQt.QtMultimedia import QAudio
        error = self.output.error()
        if (state == QAudio.IdleState and self.buffer is not None and self.buffer.atEnd() and
                error in (QAudio.NoError, QAudio.UnderrunError)):
            self.finish_requested.emit('')  # A finite memory stream has reached its end.
        elif error != QAudio.NoError:
            self.finish_requested.emit("La lecture audio a échoué. Le texte reste disponible.")
        elif state in (QAudio.IdleState, QAudio.StoppedState):
            self.finish_requested.emit('')

    @pyqtSlot()
    def cancel(self):
        self._finish()

    @pyqtSlot(str)
    def _finish(self, error=''):
        if self.done:
            return
        self.done = True
        if self.timer is not None:
            self.timer.stop()
        if self.output is not None:
            output, self.output = self.output, None
            output.stop()
            # Qt5 PulseAudio's destructor processes events. Finish it while the
            # worker and its buffer are still alive, BEFORE allowing thread exit.
            sip.delete(output)
        if self.buffer is not None:
            self.buffer.close()
            self.buffer.setData(b'')
            self.buffer.deleteLater()
            self.buffer = None
        self.pcm = b''
        if error:
            self.failed.emit(self.generation, error)
        self.finished.emit()


class Speaker(QObject):
    """One current sentence + one cached audio. Late replies cannot start playback."""
    changed = pyqtSignal()
    status = pyqtSignal(str)
    cancel_playback = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.text = ''
        self.audio = None
        self.reply = None
        self.thread = None
        self.raw = bytearray()
        self.failure = ''
        self._playback_failed = False
        self._playing = False
        self._pending_play = False
        self._generation = 0
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)

    @property
    def busy(self):
        return self.reply is not None or self.thread is not None

    def set_text(self, text):
        self.stop(clear=True)
        self.text = text
        self.changed.emit()

    def speak(self, network, key):
        if not self.text:
            return
        self.stop()
        if self.audio is not None:
            self._play()
            return
        try:
            payload = speech_payload(self.text)
            request = QNetworkRequest(QUrl(SPEECH_ENDPOINT))
            request.setHeader(QNetworkRequest.ContentTypeHeader, 'application/json')
            request.setRawHeader(b'Authorization', ('Bearer ' + key).encode('utf-8'))
            request.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.ManualRedirectPolicy)
            request.setTransferTimeout(SPEECH_TIMEOUT_MS)
            self.reply = network.post(request, json.dumps(payload, ensure_ascii=False).encode('utf-8'))
            reply = self.reply
            reply.setReadBufferSize(MAX_SPEECH_RESPONSE + 1)
            reply.readyRead.connect(self._read)
            reply.finished.connect(self._finished)
            try:
                self.timer.timeout.disconnect()
            except TypeError:
                pass
            self.timer.timeout.connect(lambda current=reply: self._timeout(current))
            self.timer.start(SPEECH_TIMEOUT_MS)
            self.status.emit("Préparation de la réponse vocale…")
            self.changed.emit()
        except (RuntimeError, ValueError):
            self.stop()
            self.status.emit("Réponse vocale indisponible. Le texte reste disponible.")

    def stop(self, clear=False):
        self._playing = self._pending_play = False
        self.timer.stop()
        if self.reply is not None:
            reply, self.reply = self.reply, None
            reply.abort()
            reply.deleteLater()
        if self.thread is not None:
            thread = self.thread
            try:
                thread.requestInterruption()
                self.cancel_playback.emit()
            except RuntimeError:  # Native cleanup may precede thread.finished delivery.
                pass
        self.raw.clear()
        self.failure = ''
        if clear:
            self.text, self.audio = '', None
        self.changed.emit()

    @pyqtSlot()
    def _read(self, reply=None):
        reply = reply if reply is not None else self.sender()
        if reply is self.reply:
            self.raw.extend(bytes(reply.readAll()))
            if len(self.raw) > MAX_SPEECH_RESPONSE and not self.failure:
                self.failure = "Réponse vocale trop volumineuse. Le texte reste disponible."
                reply.abort()

    def _timeout(self, reply):
        if reply is self.reply:
            self.failure = "Délai de réponse vocale dépassé (30 s). Le texte reste disponible."
            self.reply.abort()

    @pyqtSlot()
    def _finished(self, reply=None):
        reply = reply if reply is not None else self.sender()
        if reply is not self.reply:
            return
        self._read(reply)
        if reply is not self.reply:  # abort may synchronously emit finished.
            return
        self.timer.stop()
        self.reply = None
        try:
            status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
            if self.failure or reply.error() != QNetworkReply.NoError or status != 200:
                raise ValueError(self.failure or "Synthèse vocale indisponible (réseau, clé ou quota). Le texte reste disponible.")
            self.audio = parse_speech(bytes(self.raw))
            self._play()
        except ValueError as error:
            self.status.emit(str(error))
        finally:
            self.raw.clear()
            self.failure = ''
            reply.deleteLater()
            self.changed.emit()

    def _play(self):
        if self.thread is not None:
            self._pending_play = True  # Only the latest sentence survives cancellation.
            return
        thread = _AudioThread()
        self._generation += 1
        worker = _PlaybackWorker(self.audio, self._generation)
        thread.worker = worker
        worker.moveToThread(thread)
        self.cancel_playback.connect(worker.cancel, Qt.QueuedConnection)
        self.destroyed.connect(thread.interrupt)
        self.destroyed.connect(worker.cancel, Qt.QueuedConnection)
        self._playback_failed = False
        self._playing = True
        worker.status.connect(self._play_status)
        worker.failed.connect(self._play_failed)
        worker.finished.connect(thread.quit, Qt.DirectConnection)  # quit() is thread-safe.
        thread.finished.connect(worker.deleteLater)
        thread.started.connect(worker.start)
        thread.finished.connect(self._played)
        thread.finished.connect(lambda: _PLAYBACKS.discard(thread))
        thread.finished.connect(thread.deleteLater)
        _PLAYBACKS.add(thread)
        self.thread = thread
        thread.start()
        self.changed.emit()

    @pyqtSlot(int, str)
    def _play_status(self, generation, text):
        if self._playing and generation == self._generation:
            self.status.emit(text)

    @pyqtSlot(int, str)
    def _play_failed(self, generation, text):
        if self._playing and generation == self._generation:
            self._playback_failed = True
            self.status.emit(text)

    @pyqtSlot()
    def _played(self):
        if self.sender() is self.thread:
            self.thread = None
            if self._pending_play and self.audio is not None and self.text:
                self._pending_play = False
                self._play()
            elif self._playing and not self._playback_failed:
                self.status.emit("Lecture terminée. Vous pouvez relire la réponse.")
            if self.thread is None:
                self._playing = False
            self.changed.emit()


def wav_audio(pcm, rate, channels):
    if not 8000 <= rate <= 48000 or channels not in (1, 2):
        raise ValueError("Format microphone non accepté : PCM 16 bits, 8–48 kHz, mono ou stéréo requis.")
    frame = channels * 2
    pcm = pcm[:rate * frame * MAX_SECONDS]
    pcm = pcm[:len(pcm) // frame * frame]
    if len(pcm) < rate * frame // 4:
        raise ValueError("Audio vide ou trop court. Dictez pendant au moins un quart de seconde.")
    samples = array('h')
    samples.frombytes(pcm)
    if sys.byteorder != 'little':
        samples.byteswap()
    if max(abs(sample) for sample in samples) < 80:
        raise ValueError("Audio silencieux ou inaudible. Vérifiez le microphone et son niveau.")
    output = BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return output.getvalue()


def context_vocabulary(names, resolution=None):
    """Current draft and a few already-proposed candidates; never a database catalogue."""
    entries = [names.get(key, '') for key in ('patrimoine', 'prestataire', 'technicien', 'type_prestation')]
    if resolution:
        for key in ('patrimoine', 'prestataire', 'technicien', 'type_prestation'):
            if key in resolution['verified']:
                entries.append(resolution['verified'][key][1])
            entries.extend(row[1] for row in resolution['pending'].get(key, [])[:3])
    entries.extend(('ouvrage hydraulique', 'stockage hydraulique', 'mobilier', 'cheminement', 'végétation', 'débroussaillage',
                    'prestation', 'prestataire', 'technicien', 'date de début', 'date de fin', 'Terminé'))
    result, seen = [], set()
    for entry in entries:
        entry = ' '.join(str(entry).split())[:80]
        if entry and entry.casefold() not in seen and len(result) < 24:
            result.append(entry)
            seen.add(entry.casefold())
    return result


def transcription_multipart(audio, terms):
    if not audio or len(audio) > MAX_AUDIO_BYTES + 4096:
        raise ValueError("Audio vide ou trop volumineux pour une dictée de 60 secondes.")
    try:
        with wave.open(BytesIO(audio), 'rb') as wav:
            if (wav.getcomptype() != 'NONE' or wav.getsampwidth() != 2 or
                    wav.getnchannels() not in (1, 2) or not 8000 <= wav.getframerate() <= 48000 or
                    wav.getnframes() / wav.getframerate() > MAX_SECONDS):
                raise ValueError()
    except (wave.Error, EOFError, ValueError):
        raise ValueError("Fichier audio non accepté : un WAV PCM 16 bits de 60 secondes maximum est requis.") from None
    boundary = 'gemapi_app_' + uuid4().hex
    parts = []
    for name, value in [('model', TRANSCRIPTION_MODEL), ('language', 'fr'),
                        *[('context_bias', term) for term in terms[:24]]]:
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                      f'{value}\r\n').encode('utf-8'))
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="dictee.wav"\r\n'
                  'Content-Type: audio/wav\r\n\r\n').encode('ascii') + audio + b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode('ascii'))
    return b''.join(parts), 'multipart/form-data; boundary=' + boundary


def parse_transcription(raw):
    try:
        text = json.loads(raw)['text']
        if not isinstance(text, str) or not text.strip() or len(text) > 8000 or '\x00' in text:
            raise ValueError()
        return text.strip()
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ValueError("Voxtral n'a pas fourni de texte exploitable. Audio vide, inaudible ou réponse invalide ; poursuivez au clavier.") from None


def transcription_error(status):
    return {
        400: "Audio ou contexte non accepté par Voxtral. Réduisez la dictée et réessayez.",
        401: "Clé API Mistral invalide. Vérifiez le fichier de clé local.",
        402: "Crédit API insuffisant pour Voxtral. Vérifiez la facturation Mistral.",
        403: "Accès Voxtral non autorisé sur ce compte Mistral.",
        413: "Fichier audio trop volumineux pour Voxtral.",
        415: "Format audio non accepté par Voxtral ; un WAV PCM est requis.",
        422: "Fichier audio ou paramètres non acceptés par Voxtral.",
        429: "Quota ou crédit API Voxtral insuffisant. Vérifiez les limites et la facturation Mistral.",
    }.get(status, "Échec Voxtral : réseau interrompu ou service indisponible. Vous pouvez poursuivre au clavier.")


class _CaptureWorker(QObject):
    ready = pyqtSignal(bytes)
    failed = pyqtSignal(str)
    status = pyqtSignal(str)
    finished = pyqtSignal()
    failure_requested = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.audio = None
        self.buffer = None
        self.timer = None
        self.limit_timer = None
        self.clock = None
        self.format = None
        self.done = False
        self.failure_requested.connect(self._fail, Qt.QueuedConnection)

    @pyqtSlot()
    def start(self):
        try:
            from qgis.PyQt.QtMultimedia import QAudio, QAudioDeviceInfo, QAudioFormat, QAudioInput
        except (ImportError, OSError):
            self._fail("QtMultimedia Qt5 est indisponible dans cette installation QGIS. La saisie au clavier reste disponible.")
            return
        try:
            device = QAudioDeviceInfo.defaultInputDevice()
            if QThread.currentThread().isInterruptionRequested():
                self.cancel()
                return
            if device.isNull():
                raise ValueError("Aucun microphone disponible. Branchez ou sélectionnez un microphone dans le système.")
            fmt = QAudioFormat()
            fmt.setCodec('audio/pcm')
            fmt.setSampleRate(16000)
            fmt.setChannelCount(1)
            fmt.setSampleSize(16)
            fmt.setSampleType(QAudioFormat.SignedInt)
            fmt.setByteOrder(QAudioFormat.LittleEndian)
            if not device.isFormatSupported(fmt):
                fmt = device.nearestFormat(fmt)
            if (fmt.codec() != 'audio/pcm' or fmt.sampleSize() != 16 or
                    fmt.sampleType() != QAudioFormat.SignedInt or fmt.byteOrder() != QAudioFormat.LittleEndian or
                    not 8000 <= fmt.sampleRate() <= 48000 or fmt.channelCount() not in (1, 2)):
                raise ValueError("Le microphone ne propose pas de format WAV PCM 16 bits compatible avec Voxtral.")
            self.format = fmt
            self.buffer = QBuffer(self)
            self.buffer.open(QIODevice.WriteOnly)
            self.audio = QAudioInput(device, fmt, self)
            self.audio.stateChanged.connect(self._state_changed)
            self.audio.start(self.buffer)
            if self.audio.error() != QAudio.NoError:
                raise ValueError("Microphone inaccessible ou autorisation d'enregistrement refusée. "
                                 "Vérifiez les permissions microphone de QGIS dans Windows ou Ubuntu.")
            if self.done:
                return
            if QThread.currentThread().isInterruptionRequested():
                self.cancel()
                return
            self.clock = QElapsedTimer()
            self.clock.start()
            self.timer = QTimer(self)
            self.timer.setTimerType(Qt.PreciseTimer)
            self.timer.setInterval(100)
            self.timer.timeout.connect(self._tick)
            self.timer.start()
            self.limit_timer = QTimer(self)
            self.limit_timer.setSingleShot(True)
            self.limit_timer.setTimerType(Qt.PreciseTimer)
            self.limit_timer.timeout.connect(self.stop)
            self.limit_timer.start(MAX_SECONDS * 1000)
            self.status.emit("● Microphone actif — 0 / 60 s. Arrêter pour transcrire, ou annuler sans envoyer.")
        except Exception as error:
            self._fail(str(error) if isinstance(error, ValueError) else
                       "Microphone inaccessible. Vérifiez le périphérique et l'autorisation d'enregistrement de QGIS.")

    def _state_changed(self, state):
        from qgis.PyQt.QtMultimedia import QAudio
        if not self.done and state == QAudio.StoppedState and self.audio.error() != QAudio.NoError:
            self.failure_requested.emit("Microphone interrompu ou accès refusé. Vérifiez le périphérique et les permissions ; poursuivez au clavier.")

    def _tick(self):
        if self.done:
            return
        if QThread.currentThread().isInterruptionRequested():
            self.cancel()
            return
        elapsed = self.clock.elapsed()
        if elapsed >= MAX_SECONDS * 1000 or self.buffer.size() >= MAX_AUDIO_BYTES:
            self.stop()
        elif elapsed >= 5000 and self.buffer.size() == 0:
            self._fail("Le microphone ne fournit aucun audio. Vérifiez son accès et les autorisations d'enregistrement.")
        elif elapsed % 1000 < 100:
            self.status.emit(f"● Microphone actif — {elapsed // 1000} / 60 s. Arrêter pour transcrire, ou annuler sans envoyer.")

    def _release(self):
        self.done = True
        if self.timer is not None:
            self.timer.stop()
        if self.limit_timer is not None:
            self.limit_timer.stop()
        if self.audio is not None:
            audio, self.audio = self.audio, None
            audio.stop()
            sip.delete(audio)

    @pyqtSlot(str)
    def _fail(self, message):
        if self.done:
            return
        self._release()
        if self.buffer is not None:
            self.buffer.close()
            self.buffer.setData(b'')
        self.failed.emit(message)
        self.finished.emit()

    @pyqtSlot()
    def stop(self):
        if self.done:
            return
        if QThread.currentThread().isInterruptionRequested():
            self.cancel()
            return
        self._release()
        try:
            if self.buffer is None or self.format is None:
                raise ValueError("Audio vide. Vous pouvez poursuivre au clavier.")
            pcm = bytes(self.buffer.data())
            self.buffer.close()
            self.buffer.setData(b'')
            audio = wav_audio(pcm, self.format.sampleRate(), self.format.channelCount())
            self.ready.emit(audio)
        except ValueError as error:
            self.failed.emit(str(error))
        finally:
            self.finished.emit()

    @pyqtSlot()
    def cancel(self):
        if not self.done:
            self._release()
            if self.buffer is not None:
                self.buffer.close()
                self.buffer.setData(b'')
            self.finished.emit()


class Recorder(QObject):
    ready = pyqtSignal(bytes)
    failed = pyqtSignal(str)
    status = pyqtSignal(str)
    finished = pyqtSignal()
    stop_capture = pyqtSignal()
    cancel_capture = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.thread = None

    def start(self):
        if self.thread is not None:
            return
        thread = _AudioThread()  # Outlives a closing dialog until native capture has stopped.
        worker = _CaptureWorker()
        thread.worker = worker
        worker.moveToThread(thread)
        self.stop_capture.connect(worker.stop, Qt.QueuedConnection)
        self.cancel_capture.connect(worker.cancel, Qt.QueuedConnection)
        self.destroyed.connect(thread.interrupt)
        self.destroyed.connect(worker.cancel, Qt.QueuedConnection)
        worker.ready.connect(self.ready)
        worker.failed.connect(self.failed)
        worker.status.connect(self.status)
        worker.finished.connect(thread.quit, Qt.DirectConnection)
        thread.finished.connect(worker.deleteLater)
        thread.started.connect(worker.start)
        thread.finished.connect(self._finished)
        thread.finished.connect(lambda: _CAPTURES.discard(thread))
        thread.finished.connect(thread.deleteLater)
        _CAPTURES.add(thread)
        self.thread = thread
        thread.start()

    def stop(self):
        if self.thread is not None:
            try:
                self.stop_capture.emit()
            except RuntimeError:  # Native teardown may precede delivery of thread.finished.
                pass

    def cancel(self):
        if self.thread is not None:
            try:
                self.thread.requestInterruption()
                self.cancel_capture.emit()
            except RuntimeError:
                pass

    def _finished(self):
        self.thread = None
        self.finished.emit()
