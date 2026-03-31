/**
 * Oracle page interactivity.
 * External file to comply with CSP (script-src 'self').
 */

async function askOracle() {
    const topicId = document.getElementById('oracle-topic-select').value;
    const question = document.getElementById('oracle-question').value;
    const btn = document.getElementById('oracle-ask-btn');
    const responseDiv = document.getElementById('oracle-response');

    if (!topicId && !question) {
        alert('Select a topic or enter a question');
        return;
    }

    btn.disabled = true;
    btn.textContent = 'Thinking...';
    responseDiv.className = 'oracle-response visible';
    responseDiv.textContent = 'Oracle is analyzing... (this may take 30-60 seconds)';

    try {
        const csrfMeta = document.body.getAttribute('hx-headers');
        let csrfToken = '';
        if (csrfMeta) {
            try { csrfToken = JSON.parse(csrfMeta)['X-CSRF-Token'] || ''; } catch(e) {}
        }

        const resp = await fetch('/api/oracle/ask', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': csrfToken,
            },
            body: JSON.stringify({ topic_id: topicId, question: question }),
        });
        const data = await resp.json();

        if (data.error) {
            responseDiv.textContent = 'Error: ' + data.error;
            responseDiv.style.color = 'var(--red)';
        } else {
            // Render analysis + sources
            let text = data.analysis || 'No analysis returned.';
            text += '\n\n---\n';
            text += 'Topic: ' + (data.topic_name || topicId);
            text += '  |  Articles analyzed: ' + (data.article_count || 0);
            text += '  |  ' + new Date(data.timestamp).toLocaleString();

            if (data.sources && data.sources.length > 0) {
                text += '\n\nSOURCES:\n';
                // Deduplicate by source name
                const sourceCounts = {};
                data.sources.forEach(function(s) {
                    const name = s.source || 'Unknown';
                    sourceCounts[name] = (sourceCounts[name] || 0) + 1;
                });
                Object.entries(sourceCounts)
                    .sort(function(a, b) { return b[1] - a[1]; })
                    .forEach(function(entry) {
                        text += '  ' + entry[0] + ' (' + entry[1] + ' articles)\n';
                    });
                text += '\nARTICLES:\n';
                data.sources.slice(0, 15).forEach(function(s) {
                    text += '  - ' + s.title + (s.source ? ' [' + s.source + ']' : '') + '\n';
                });
                if (data.sources.length > 15) {
                    text += '  ... and ' + (data.sources.length - 15) + ' more\n';
                }
            }

            responseDiv.textContent = text;
            responseDiv.style.color = '';
        }
    } catch (e) {
        responseDiv.textContent = 'Request failed: ' + e.message;
        responseDiv.style.color = 'var(--red)';
    } finally {
        btn.disabled = false;
        btn.textContent = 'Ask Oracle';
    }
}

// Click topic card to select it in dropdown
document.addEventListener('click', function(e) {
    const card = e.target.closest('.oracle-topic');
    if (card) {
        const tid = card.dataset.topicId;
        const select = document.getElementById('oracle-topic-select');
        if (select) {
            select.value = tid;
            document.querySelectorAll('.oracle-topic').forEach(function(c) { c.classList.remove('selected'); });
            card.classList.add('selected');
        }
    }
});

// Ask Oracle button
document.addEventListener('DOMContentLoaded', function() {
    const askBtn = document.getElementById('oracle-ask-btn');
    if (askBtn) {
        askBtn.addEventListener('click', askOracle);
    }
});

// Category filter
document.addEventListener('click', function(e) {
    if (e.target.classList.contains('oracle-filter-btn')) {
        const cat = e.target.dataset.cat;
        document.querySelectorAll('.oracle-filter-btn').forEach(function(b) { b.classList.remove('active'); });
        e.target.classList.add('active');
        document.querySelectorAll('.oracle-topic').forEach(function(card) {
            if (cat === 'all' || card.dataset.category === cat) {
                card.style.display = '';
            } else {
                card.style.display = 'none';
            }
        });
    }
});
