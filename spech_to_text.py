import speech_recognition as sr 
import speak


def spech_to_text():
    r = sr.Recognizer()
    try:
      with sr.Microphone() as source:
        audio = r.listen(source)
    except Exception:
      return "microphone is not available"

    try:
      voice_data = r.recognize_google(audio)
      return voice_data
    except sr.UnknownValueError:
      speak.speak("sorry")
      return "sorry"
    except sr.RequestError:
      speak.speak("No internet connection, please turn on your internet")
      return "No internet connection, please turn on your internet"



