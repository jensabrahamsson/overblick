"""
Prompt templates for the email agent plugin.

All decision-making is prompt-driven — no hard-coded if/else for email routing.
Each template is a function that builds the prompt from context variables.
"""


def classification_prompt(
    goals: str,
    learnings: str,
    sender_history: str,
    sender: str,
    subject: str,
    body: str,
) -> list[dict[str, str]]:
    """Build the email classification prompt chain."""
    system = (
        "You are Stal's email classification system. Given an incoming email, "
        "decide the appropriate action.\n\n"
        f"Current goals:\n{goals}\n\n"
        f"Recent learnings:\n{learnings}\n\n"
        f"Sender history:\n{sender_history}\n\n"
        "Allowed reply addresses: jens.abrahamsson@wirelesscar.com\n\n"
        "Actions:\n"
        "- IGNORE: Not relevant, spam, newsletters, automated notifications\n"
        "- NOTIFY: Important but doesn't need a reply — notify Jens on Telegram\n"
        "- REPLY: Needs a response — draft a reply as Jens's assistant\n"
        "- ASK_BOSS: Uncertain — ask the supervisor for guidance\n\n"
        'Respond in JSON ONLY:\n'
        '{"intent": "ignore|notify|reply|ask_boss", "confidence": 0.0-1.0, '
        '"reasoning": "...", "priority": "low|normal|high|urgent"}'
    )

    user = (
        f"From: {sender}\n"
        f"Subject: {subject}\n"
        f"Body:\n{body}"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def reply_prompt(
    sender: str,
    subject: str,
    body: str,
    sender_context: str,
    interaction_history: str,
) -> list[dict[str, str]]:
    """Build the email reply generation prompt."""
    system = (
        "You are Stal, Jens Abrahamsson's executive secretary, writing an email "
        "reply on his behalf.\n\n"
        "CRITICAL: Respond in the SAME LANGUAGE as the incoming email.\n"
        "Keep it professional, concise, and helpful. Sign as "
        '"Best regards, Jens" (not as an assistant).\n'
        "If you're unsure about specific details, say you'll follow up.\n\n"
        "GDPR POLICY: You retain email content for 30 days only. If someone "
        "references information from a conversation older than 30 days that you "
        "cannot find, politely explain that due to GDPR compliance, detailed "
        "correspondence is not retained beyond 30 days, and ask them to resend "
        "the relevant information.\n\n"
        f"Context about the sender:\n{sender_context}\n\n"
        f"Previous interactions:\n{interaction_history}"
    )

    user = (
        "Email to reply to:\n"
        f"From: {sender}\n"
        f"Subject: {subject}\n"
        f"Body:\n{body}\n\n"
        "Write the reply (in the same language as the email above):"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def notification_prompt(
    sender: str,
    subject: str,
    body: str,
) -> list[dict[str, str]]:
    """Build the Telegram notification summary prompt."""
    system = (
        "Summarize this email in 2-3 sentences for a Telegram notification to Jens.\n"
        "Include: who sent it, what it's about, and why it's worth attention.\n"
        "Be concise — this is a mobile notification."
    )

    user = (
        f"From: {sender}\n"
        f"Subject: {subject}\n"
        f"Body:\n{body}"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def boss_consultation_prompt(
    sender: str,
    subject: str,
    snippet: str,
    reasoning: str,
    tentative_intent: str,
    confidence: float,
) -> list[dict[str, str]]:
    """Build the boss consultation question prompt."""
    system = (
        "You're uncertain about how to handle this email. Formulate a brief "
        "question for the supervisor. Include the email context and explain "
        "what you're unsure about."
    )

    user = (
        f"Email:\nFrom: {sender}\nSubject: {subject}\nSnippet: {snippet}\n\n"
        f"Your reasoning so far: {reasoning}\n"
        f"Your tentative classification: {tentative_intent}\n"
        f"Confidence: {confidence}\n\n"
        "Formulate your question:"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
