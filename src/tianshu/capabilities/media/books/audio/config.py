"""Chapter cache identity; speech configuration belongs exclusively to speech."""
from ....contracts import digest

def profile_id(value):return digest(value)[:32]
