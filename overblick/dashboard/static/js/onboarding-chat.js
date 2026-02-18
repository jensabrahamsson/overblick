/**
 * Onboarding Chat Component
 *
 * Floating chat panel for testing identities during onboarding.
 * Requires: identity selector, LLM config, CSRF token.
 */
(function() {
    'use strict';

    var chatOpen = false;
    var chatHistory = [];
    var isLoading = false;
    var csrfToken = '';
    var chatEndpoint = '/onboard/chat';

    function init() {
        var btn = document.getElementById('onboarding-chat-btn');
        var panel = document.getElementById('onboarding-chat-panel');
        if (!btn || !panel) return;

        csrfToken = document.body.getAttribute('hx-headers');
        if (csrfToken) {
            try {
                var parsed = JSON.parse(csrfToken.replace(/'/g, '"'));
                csrfToken = parsed['X-CSRF-Token'] || '';
            } catch (e) {
                csrfToken = '';
            }
        }

        // Detect if we're in setup wizard (different endpoint, no CSRF)
        if (document.querySelector('[data-chat-endpoint]')) {
            chatEndpoint = document.querySelector('[data-chat-endpoint]').getAttribute('data-chat-endpoint');
        }

        btn.addEventListener('click', toggleChat);

        var closeBtn = panel.querySelector('.chat-close');
        if (closeBtn) closeBtn.addEventListener('click', toggleChat);

        var form = panel.querySelector('.chat-input-form');
        if (form) form.addEventListener('submit', sendMessage);
    }

    function toggleChat() {
        var panel = document.getElementById('onboarding-chat-panel');
        var btn = document.getElementById('onboarding-chat-btn');
        if (!panel) return;

        chatOpen = !chatOpen;
        panel.style.display = chatOpen ? 'flex' : 'none';
        if (btn) btn.setAttribute('aria-expanded', chatOpen);

        if (chatOpen) {
            var input = panel.querySelector('.chat-input');
            if (input) input.focus();
        }
    }

    function getSelectedIdentity() {
        var sel = document.getElementById('chat-identity-select');
        return sel ? sel.value : '';
    }

    function sendMessage(e) {
        e.preventDefault();
        if (isLoading) return;

        var input = document.querySelector('.chat-input');
        var msg = input ? input.value.trim() : '';
        if (!msg) return;

        var identity = getSelectedIdentity();
        if (!identity) {
            addSystemMessage('Select an identity to chat with.');
            return;
        }

        addMessage('user', msg);
        input.value = '';
        isLoading = true;
        showTyping(true);

        var headers = { 'Content-Type': 'application/json' };
        if (csrfToken) headers['X-CSRF-Token'] = csrfToken;

        fetch(chatEndpoint, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({
                identity_name: identity,
                message: msg,
            }),
        })
        .then(function(resp) { return resp.json(); })
        .then(function(data) {
            showTyping(false);
            isLoading = false;

            if (data.success && data.response) {
                addMessage('assistant', data.response, identity);
            } else {
                addSystemMessage(data.error || 'No response received.');
            }
        })
        .catch(function(err) {
            showTyping(false);
            isLoading = false;
            addSystemMessage('Connection error: ' + err.message);
        });
    }

    function addMessage(role, content, identity) {
        var feed = document.querySelector('.chat-messages');
        if (!feed) return;

        var div = document.createElement('div');
        div.className = 'chat-msg chat-msg--' + role;

        var label = document.createElement('div');
        label.className = 'chat-msg-label';
        label.textContent = role === 'user' ? 'You' : (identity || 'Agent');

        var text = document.createElement('div');
        text.className = 'chat-msg-text';
        text.textContent = content;

        div.appendChild(label);
        div.appendChild(text);
        feed.appendChild(div);
        feed.scrollTop = feed.scrollHeight;

        chatHistory.push({ role: role, content: content });
    }

    function addSystemMessage(text) {
        var feed = document.querySelector('.chat-messages');
        if (!feed) return;

        var div = document.createElement('div');
        div.className = 'chat-msg chat-msg--system';
        div.textContent = text;
        feed.appendChild(div);
        feed.scrollTop = feed.scrollHeight;
    }

    function showTyping(show) {
        var indicator = document.querySelector('.chat-typing');
        if (indicator) indicator.style.display = show ? 'block' : 'none';
    }

    // Init on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
