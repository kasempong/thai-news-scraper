from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import json
import sqlite3
from collections import Counter
import re
from urllib.parse import urljoin
import logging
import time
import os

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Database setup
DB_PATH = 'thai_news.db'

def init_db():
    """Initialize database - simplified version"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    try:
        # Main news table
        c.execute('''CREATE TABLE IF NOT EXISTS news
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      title TEXT,
                      summary TEXT,
                      source TEXT,
                      url TEXT UNIQUE,
                      published_date TEXT,
                      raw_content TEXT,
                      keywords TEXT,
                      sentiment_score REAL,
                      viral_score REAL,
                      category TEXT,
                      scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        # Create index for faster queries
        c.execute('CREATE INDEX IF NOT EXISTS idx_published_date ON news(published_date)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_viral_score ON news(viral_score)')
        
        conn.commit()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database init error: {e}")
    finally:
        conn.close()

init_db()

# News sources configuration
NEWS_SOURCES = {
    'manager_online': {
        'url': 'https://www.manager.co.th',
        'selector': '.news-item',
        'category': 'general'
    },
    'thai_pbs': {
        'url': 'https://www.thaipbsworld.com',
        'selector': '.story-card',
        'category': 'general'
    },
    'thairath': {
        'url': 'https://www.thairath.co.th',
        'selector': '.item-news',
        'category': 'general'
    }
}

def get_viral_score(title, keywords=''):
    """Calculate viral score based on keywords and title"""
    viral_keywords = ['ไวรัล', 'ข่าวลือ', 'ดราม่า', 'ปะทะ', 'ชาวเน็ต', 'พูดถึง', 'ทวิตเตอร์']
    score = 50
    
    title_lower = title.lower() if title else ''
    for keyword in viral_keywords:
        if keyword in title_lower:
            score += 15
    
    return min(score, 100)

def scrape_news_item(url):
    """Scrape a single news item"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.encoding = 'utf-8'
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract title
        title_elem = soup.find('h1') or soup.find('h2') or soup.find('title')
        title = title_elem.get_text(strip=True) if title_elem else 'Unknown'
        
        # Extract summary
        summary_elem = soup.find('p', class_=['summary', 'description', 'lead'])
        summary = summary_elem.get_text(strip=True) if summary_elem else ''
        
        return {
            'title': title,
            'summary': summary,
            'url': url,
            'viral_score': get_viral_score(title),
            'published_date': datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error scraping {url}: {e}")
        return None

def save_articles(articles):
    """Save articles to database"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    try:
        for article in articles:
            if article:
                c.execute('''INSERT OR REPLACE INTO news 
                           (title, summary, url, viral_score, published_date, source)
                           VALUES (?, ?, ?, ?, ?, ?)''',
                         (article['title'], article['summary'], article['url'],
                          article['viral_score'], article['published_date'], 'scraped'))
        conn.commit()
        logger.info(f"Saved {len([a for a in articles if a])} articles")
    except Exception as e:
        logger.error(f"Error saving articles: {e}")
    finally:
        conn.close()

# API Endpoints

@app.route('/api/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'version': '1.0',
        'message': 'News scraper API is running'
    }), 200

@app.route('/api/articles', methods=['GET'])
def get_articles():
    """Get articles from database"""
    try:
        limit = request.args.get('limit', 50, type=int)
        
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        c.execute('''SELECT * FROM news 
                   ORDER BY viral_score DESC, published_date DESC 
                   LIMIT ?''', (limit,))
        
        articles = [dict(row) for row in c.fetchall()]
        conn.close()
        
        return jsonify({
            'success': True,
            'count': len(articles),
            'articles': articles
        }), 200
    except Exception as e:
        logger.error(f"Error getting articles: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/trending', methods=['GET'])
def get_trending():
    """Get trending topics"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        c.execute('''SELECT title, viral_score, COUNT(*) as mentions
                   FROM news 
                   GROUP BY title
                   ORDER BY viral_score DESC 
                   LIMIT 20''')
        
        trends = [dict(row) for row in c.fetchall()]
        conn.close()
        
        return jsonify({
            'success': True,
            'trends': trends
        }), 200
    except Exception as e:
        logger.error(f"Error getting trends: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/scrape', methods=['POST'])
def scrape_now():
    """Trigger scraping"""
    try:
        articles = []
        
        for source_name, config in list(NEWS_SOURCES.items())[:1]:  # Scrape just one for now
            try:
                response = requests.get(config['url'], timeout=10)
                soup = BeautifulSoup(response.text, 'html.parser')
                
                items = soup.find_all('a', href=True)[:10]
                
                for item in items:
                    href = item.get('href', '')
                    if href.startswith('http') or href.startswith('/'):
                        url = href if href.startswith('http') else config['url'] + href
                        article = scrape_news_item(url)
                        if article:
                            article['source'] = source_name
                            articles.append(article)
            except Exception as e:
                logger.error(f"Error scraping {source_name}: {e}")
        
        save_articles(articles)
        
        return jsonify({
            'success': True,
            'scraped': len(articles),
            'message': f'Scraped {len(articles)} articles'
        }), 200
    except Exception as e:
        logger.error(f"Error in scrape endpoint: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/', methods=['GET'])
def index():
    """Root endpoint"""
    return jsonify({
        'name': 'Thai News Scraper API',
        'version': '1.0',
        'endpoints': {
            'health': '/api/health',
            'articles': '/api/articles',
            'trending': '/api/trending',
            'scrape': '/api/scrape'
        }
    }), 200

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
