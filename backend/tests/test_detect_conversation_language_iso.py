from services.conversation_language import detect_conversation_language_iso


def test_detect_conversation_language_iso():
    french = (
        "Bonjour, je souhaite construire une stratégie de trading algorithmique. "
        "Merci de décrire les signaux d'entrée et de sortie en détail. "
    ) * 25
    assert detect_conversation_language_iso([{"role": "user", "content": french}]) == "fr"
    assert detect_conversation_language_iso([]) == ""
    assert detect_conversation_language_iso([{"role": "user", "content": "   "}]) == ""
