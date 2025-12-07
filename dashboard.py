from flask import Flask, render_template_string
import database as db

app = Flask(__name__)

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Очередь на сдачу</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a2e;
            color: #eee;
            padding: 20px;
            min-height: 100vh;
        }
        h1 {
            text-align: center;
            margin-bottom: 30px;
            color: #00d4ff;
        }
        .events-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 20px;
            max-width: 1200px;
            margin: 0 auto;
        }
        .event-card {
            background: #16213e;
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.3);
        }
        .event-title {
            font-size: 1.3em;
            color: #00d4ff;
            margin-bottom: 10px;
            border-bottom: 1px solid #333;
            padding-bottom: 10px;
        }
        .event-stats {
            color: #888;
            margin-bottom: 15px;
            font-size: 0.9em;
        }
        .queue-list {
            list-style: none;
        }
        .queue-item {
            display: flex;
            justify-content: space-between;
            padding: 8px 12px;
            background: #0f3460;
            margin-bottom: 5px;
            border-radius: 6px;
        }
        .queue-item:hover {
            background: #1a4a7a;
        }
        .position {
            color: #00d4ff;
            font-weight: bold;
            min-width: 30px;
        }
        .name {
            flex: 1;
            margin-left: 10px;
        }
        .empty {
            color: #666;
            font-style: italic;
            text-align: center;
            padding: 20px;
        }
        .refresh-note {
            text-align: center;
            color: #666;
            margin-top: 30px;
            font-size: 0.85em;
        }
    </style>
</head>
<body>
    <h1>Очередь на сдачу работ</h1>
    
    <div class="events-grid">
        {% for event_id, event in data.items() %}
        <div class="event-card">
            <div class="event-title">{{ event.name }}</div>
            <div class="event-stats">
                Занято: {{ event.queue|length }} / {{ event.max_positions }}
            </div>
            
            {% if event.queue %}
            <ul class="queue-list">
                {% for item in event.queue %}
                <li class="queue-item">
                    <span class="position">{{ item.position }}</span>
                    <span class="name">{{ item.first_name or item.username or '—' }}</span>
                </li>
                {% endfor %}
            </ul>
            {% else %}
            <div class="empty">Очередь пуста</div>
            {% endif %}
        </div>
        {% endfor %}
        
        {% if not data %}
        <div class="empty">Событий пока нет</div>
        {% endif %}
    </div>
    
    <p class="refresh-note">Обновите страницу для актуальных данных</p>
</body>
</html>
"""


@app.route("/")
def dashboard():
    db.init_db()
    data = db.get_all_data()
    return render_template_string(DASHBOARD_HTML, data=data)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
