import os
import json
from datetime import datetime

from flask import (
    Flask, render_template, request,
    redirect, url_for, flash, jsonify
)
from flask_sqlalchemy import SQLAlchemy
import requests

# ============================
# Config
# ============================

# Use your key – you can keep env var OR fallback to string for now
WEATHER_API_KEY = os.environ.get("OPENWEATHER_API_KEY", "54c3e5436355a0e40913a9976b9e75ef")

app = Flask(__name__, static_folder="statics")
app.config["SECRET_KEY"] = "dev-secret-key"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///weather.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


# ============================
# Model
# ============================

class WeatherQuery(db.Model):
    __tablename__ = "weather_queries"

    id = db.Column(db.Integer, primary_key=True)
    user_input = db.Column(db.String(255), nullable=False)
    normalized_name = db.Column(db.String(255))
    lat = db.Column(db.Float)
    lon = db.Column(db.Float)
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    current_weather_json = db.Column(db.Text)
    forecast_json = db.Column(db.Text)

    def __repr__(self):
        return f"<WeatherQuery {self.id} {self.normalized_name}>"

with app.app_context():
    db.create_all()


# ============================
# Helpers
# ============================

def parse_date(d: str):
    if not d:
        return None
    return datetime.strptime(d, "%Y-%m-%d").date()


def geocode_location(query: str):
    if not WEATHER_API_KEY:
        raise RuntimeError("OPENWEATHER_API_KEY is not set")

    url = "http://api.openweathermap.org/geo/1.0/direct"
    params = {"q": query, "limit": 1, "appid": WEATHER_API_KEY}
    r = requests.get(url, params=params, timeout=5)
    r.raise_for_status()
    data = r.json()
    if not data:
        return None

    loc = data[0]
    return {
        "name": f"{loc.get('name')}, {loc.get('country')}",
        "lat": loc["lat"],
        "lon": loc["lon"],
    }


def get_weather_and_forecast(lat: float, lon: float):
    if not WEATHER_API_KEY:
        raise RuntimeError("OPENWEATHER_API_KEY is not set")

    params = {"lat": lat, "lon": lon, "appid": WEATHER_API_KEY, "units": "metric"}

    current_resp = requests.get(
        "https://api.openweathermap.org/data/2.5/weather",
        params=params,
        timeout=5,
    )
    current_resp.raise_for_status()

    forecast_resp = requests.get(
        "https://api.openweathermap.org/data/2.5/forecast",
        params=params,
        timeout=5,
    )
    forecast_resp.raise_for_status()

    return current_resp.json(), forecast_resp.json()


# ============================
# Routes
# ============================

@app.route("/", methods=["GET", "POST"])
def index():
    weather_data = None
    forecast_data = None
    query_obj = None

    if request.method == "POST":
        location = request.form.get("location", "").strip()
        start_date_raw = request.form.get("start_date")
        end_date_raw = request.form.get("end_date")

        if not location:
            flash("Please enter a location.", "error")
            return redirect(url_for("index"))

        try:
            start_date = parse_date(start_date_raw)
            end_date = parse_date(end_date_raw)
        except ValueError:
            flash("Invalid date format. Use YYYY-MM-DD.", "error")
            return redirect(url_for("index"))

        if start_date and end_date and start_date > end_date:
            flash("Start date must be before end date.", "error")
            return redirect(url_for("index"))

        try:
            loc = geocode_location(location)
        except Exception as e:
            print("Geocode error:", e)
            flash("Error contacting geocoding service.", "error")
            return redirect(url_for("index"))

        if not loc:
            flash("Could not find that location. Try a more specific name.", "error")
            return redirect(url_for("index"))

        try:
            current, forecast = get_weather_and_forecast(loc["lat"], loc["lon"])
        except Exception as e:
            print("Weather API error:", e)
            flash("Error retrieving weather data.", "error")
            return redirect(url_for("index"))

        query_obj = WeatherQuery(
            user_input=location,
            normalized_name=loc["name"],
            lat=loc["lat"],
            lon=loc["lon"],
            start_date=start_date,
            end_date=end_date,
            current_weather_json=json.dumps(current),
            forecast_json=json.dumps(forecast),
        )
        db.session.add(query_obj)
        db.session.commit()

        weather_data = current
        forecast_data = forecast

    return render_template(
        "index.html",
        weather=weather_data,
        forecast=forecast_data,
        query=query_obj,
    )


@app.route("/history")
def history():
    queries = WeatherQuery.query.order_by(WeatherQuery.created_at.desc()).all()
    return render_template("history.html", queries=queries)


@app.route("/history/<int:query_id>")
def detail(query_id):
    q = WeatherQuery.query.get_or_404(query_id)
    return render_template(
        "detail.html",
        query=q,
        weather=json.loads(q.current_weather_json) if q.current_weather_json else None,
        forecast=json.loads(q.forecast_json) if q.forecast_json else None,
    )


@app.route("/history/<int:query_id>/edit", methods=["GET", "POST"])
def edit(query_id):
    q = WeatherQuery.query.get_or_404(query_id)

    if request.method == "POST":
        location = request.form.get("location", "").strip()
        start_date_raw = request.form.get("start_date")
        end_date_raw = request.form.get("end_date")

        if not location:
            flash("Location cannot be empty.", "error")
            return redirect(url_for("edit", query_id=q.id))

        try:
            start_date = parse_date(start_date_raw)
            end_date = parse_date(end_date_raw)
        except ValueError:
            flash("Invalid date format.", "error")
            return redirect(url_for("edit", query_id=q.id))

        if start_date and end_date and start_date > end_date:
            flash("Start date must be before end date.", "error")
            return redirect(url_for("edit", query_id=q.id))

        loc = geocode_location(location)
        if not loc:
            flash("Could not find that location.", "error")
            return redirect(url_for("edit", query_id=q.id))

        current, forecast = get_weather_and_forecast(loc["lat"], loc["lon"])

        q.user_input = location
        q.normalized_name = loc["name"]
        q.lat = loc["lat"]
        q.lon = loc["lon"]
        q.start_date = start_date
        q.end_date = end_date
        q.current_weather_json = json.dumps(current)
        q.forecast_json = json.dumps(forecast)

        db.session.commit()
        flash("Record updated.", "success")
        return redirect(url_for("detail", query_id=q.id))

    return render_template("edit.html", query=q)


@app.route("/history/<int:query_id>/delete", methods=["POST"])
def delete(query_id):
    q = WeatherQuery.query.get_or_404(query_id)
    db.session.delete(q)
    db.session.commit()
    flash("Record deleted.", "success")
    return redirect(url_for("history"))


@app.route("/by-coords", methods=["POST"])
def by_coords():
    data = request.get_json()
    lat = data.get("lat")
    lon = data.get("lon")

    if lat is None or lon is None:
        return jsonify({"error": "Missing coordinates"}), 400

    try:
        current, forecast = get_weather_and_forecast(lat, lon)
    except Exception as e:
        print("Weather API error:", e)
        return jsonify({"error": "Weather API error"}), 500

    return jsonify({"current": current, "forecast": forecast})


@app.route("/export/<fmt>")
def export(fmt):
    queries = WeatherQuery.query.order_by(WeatherQuery.created_at).all()

    if fmt == "json":
        data = []
        for q in queries:
            data.append(
                {
                    "id": q.id,
                    "user_input": q.user_input,
                    "normalized_name": q.normalized_name,  # <-- fixed here
                    "lat": q.lat,
                    "lon": q.lon,
                    "start_date": str(q.start_date) if q.start_date else None,
                    "end_date": str(q.end_date) if q.end_date else None,
                    "created_at": q.created_at.isoformat(),
                }
            )
        return jsonify(data)

    if fmt == "csv":
        import csv
        from io import StringIO

        si = StringIO()
        writer = csv.writer(si)
        writer.writerow(
            ["id", "user_input", "normalized_name", "lat", "lon",
             "start_date", "end_date", "created_at"]
        )
        for q in queries:
            writer.writerow(
                [
                    q.id,
                    q.user_input,
                    q.normalized_name,
                    q.lat,
                    q.lon,
                    q.start_date,
                    q.end_date,
                    q.created_at.isoformat(),
                ]
            )
        output = si.getvalue()
        return app.response_class(
            output,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment;filename=weather_queries.csv"},
        )

    return jsonify({"error": "Unsupported format"}), 400


# ============================
# Main
# ============================

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
