from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import json
import asyncio
from threading import Thread
import sqlite3
from collections import Counter
import re
from urllib.parse import urljoin
import logging
import time

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Database setup
DB_PATH = 'thai_news.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
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
                  deep_dive_highlights TEXT,
                  scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # Create index on published_date for faster filtering
    try:
        c.execute('CREATE INDEX IF NOT EXISTS idx_published_date ON news(published_date)')
    except:
        pass
    
    # Create table for X.com trending hashtags
    c.execute('''CREATE TABLE IF NOT EXISTS x_trending_hashtags
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  hashtag TEXT NOT NULL,
                  count INTEGER,
                  source TEXT,
                  scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  position INTEGER)''')
    
    # Create table for hashtag history (track trends over time)
    c.execute('''CREATE TABLE IF NOT EXISTS hashtag_history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  hashtag TEXT NOT NULL,
                  count INTEGER,
                  date DATE,
                  rank INTEGER)''')
    
    try:
        c.execute('CREATE INDEX IF NOT EXISTS idx_hashtag ON x_trending_hashtags(hashtag)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_hashtag_date ON hashtag_history(date)')
    except:
        pass
    
    conn.commit()
    conn.close()
    
    c.execute('''CREATE TABLE IF NOT EXISTS trending_topics
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  topic TEXT UNIQUE,
                  frequency INTEGER,
                  related_keywords TEXT,
                  sentiment_avg REAL,
                  articles_count INTEGER,
                  last_updated TIMESTAMP,
                  investigation_angles TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS investigation_logs
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  topic TEXT,
                  angle TEXT,
                  findings TEXT,
                  status TEXT,
                  created_at TIMESTAMP,
                  updated_at TIMESTAMP)''')
    
    conn.commit()
    conn.close()

init_db()

# ============ NEWS SOURCES CONFIGURATION - THAI LANGUAGE FOCUS + SOCIAL MEDIA ============
SOURCES = {
    'thai_rath': {
        'url': 'https://www.thairath.co.th',
        'selector': '.list-news article',
        'title': '.head-news h4 a',
        'link': '.head-news h4 a',
        'snippet': '.detail-news p',
        'language': 'th'
    },
    'matichon': {
        'url': 'https://www.matichon.co.th',
        'selector': '.item-news',
        'title': '.title-news a',
        'link': '.title-news a',
        'snippet': '.detail-news',
        'language': 'th'
    },
    'khaosod': {
        'url': 'https://www.khaosod.co.th',
        'selector': 'article.item-content',
        'title': 'h2.item-title a',
        'link': 'h2.item-title a',
        'snippet': 'p.item-desc',
        'language': 'th'
    },
    'bangkokbiznews': {
        'url': 'https://www.bangkokbiznews.com/news',
        'selector': 'article.item-news',
        'title': 'h3 a',
        'link': 'h3 a',
        'snippet': '.summary',
        'language': 'th'
    },
    'sanook': {
        'url': 'https://www.sanook.com/news',
        'selector': '.article-item',
        'title': '.article-title a',
        'link': '.article-title a',
        'snippet': '.article-description',
        'language': 'th'
    },
    'thairath_topnews': {
        'url': 'https://www.thairath.co.th/topnews',
        'selector': '.list-news-top article',
        'title': 'h4 a',
        'link': 'h4 a',
        'snippet': 'p.detail-news',
        'language': 'th'
    },
    # SOCIAL MEDIA SOURCES FOR GOSSIP/DRAMA/CELEBRITY
    'twitter_x_trending': {
        'url': 'https://twitter.com/search?q=ดราม่า OR ดารา OR อินฟลูเอนเซอร์ lang:th&src=typed_query&f=live',
        'selector': 'article[data-testid="tweet"]',
        'title': 'div[data-testid="tweetText"]',
        'link': 'a[href*="/status/"]',
        'snippet': 'div[data-testid="tweetText"]',
        'language': 'th',
        'source_type': 'social_media'
    },
    'tiktok_trending': {
        'url': 'https://www.tiktok.com/discover?keywords=ดราม่า',
        'selector': 'div[data-testid="UserCard"]',
        'title': 'a.tiktok-1g4p9j8',
        'link': 'a[href*="/video/"]',
        'snippet': 'p.video-desc',
        'language': 'th',
        'source_type': 'social_media'
    },
    'facebook_groups': {
        'url': 'https://www.facebook.com/groups/',
        'selector': '.x193iq51',
        'title': '.x1llihdp h3',
        'link': 'a[href*="/posts/"]',
        'snippet': '.x193iq51 span',
        'language': 'th',
        'source_type': 'social_media'
    },
    'instagram_hashtags': {
        'url': 'https://www.instagram.com/explore/tags/',
        'selector': 'article._aagu',
        'title': 'span.x1llihdp',
        'link': 'a[href*="/p/"]',
        'snippet': 'span._a9zs',
        'language': 'th',
        'source_type': 'social_media'
    },
    'pantip_gossip': {
        'url': 'https://www.pantip.com/forum/topic/gossip-news',
        'selector': '.item-topic-title',
        'title': '.item-topic-title h2 a',
        'link': '.item-topic-title h2 a',
        'snippet': '.item-topic-description',
        'language': 'th'
    },
    'sanook_gossip': {
        'url': 'https://www.sanook.com/gossip',
        'selector': '.item-news',
        'title': '.item-title a',
        'link': '.item-title a',
        'snippet': '.item-desc',
        'language': 'th'
    }
}

# ============ NEWS SCRAPER ============
class NewsScraperThai:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def extract_publish_date(self, article_text, url):
        """Try to extract publication date from article"""
        # Look for common Thai date patterns
        date_patterns = [
            r'(\d{1,2})\s*(มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม)',  # Thai months
            r'(\d{1,2})/(\d{1,2})/(\d{2,4})',  # DD/MM/YYYY
            r'(\d{4})-(\d{1,2})-(\d{1,2})',  # YYYY-MM-DD
        ]
        
        try:
            for pattern in date_patterns:
                match = re.search(pattern, article_text)
                if match:
                    # Return the matched date string (will be parsed later)
                    return match.group(0)
        except:
            pass
        
        # If no date found, use current date
        return datetime.now().isoformat()
    
    def scrape_source(self, source_name, config):
        """Scrape a single news source"""
        try:
            response = requests.get(config['url'], headers=self.headers, timeout=10)
            response.encoding = 'utf-8'
            soup = BeautifulSoup(response.content, 'html.parser')
            
            articles = []
            items = soup.select(config['selector'])[:15]  # Top 15 articles
            
            for item in items:
                try:
                    title_elem = item.select_one(config['title'])
                    link_elem = item.select_one(config['link'])
                    snippet_elem = item.select_one(config['snippet'])
                    
                    if title_elem and link_elem:
                        title = title_elem.get_text(strip=True)
                        link = link_elem.get('href', '')
                        if not link.startswith('http'):
                            link = urljoin(config['url'], link)
                        snippet = snippet_elem.get_text(strip=True) if snippet_elem else ''
                        
                        # Try to extract publish date
                        article_full_text = title + ' ' + snippet
                        publish_date = self.extract_publish_date(article_full_text, link)
                        
                        articles.append({
                            'title': title,
                            'url': link,
                            'snippet': snippet,
                            'source': source_name,
                            'published_date': publish_date,
                            'scraped_at': datetime.now().isoformat()
                        })
                except Exception as e:
                    logger.error(f"Error parsing article in {source_name}: {e}")
                    continue
            
            return articles
        except Exception as e:
            logger.error(f"Error scraping {source_name}: {e}")
            return []
    
    def scrape_all(self):
        """Scrape all configured sources"""
        all_articles = []
        for source_name, config in SOURCES.items():
            articles = self.scrape_source(source_name, config)
            all_articles.extend(articles)
            logger.info(f"Scraped {len(articles)} articles from {source_name}")
        return all_articles
    
    def scrape_x_trending_hashtags(self):
        """Scrape top 10 trending hashtags from X.com"""
        try:
            hashtags = []
            
            # Method 1: Try to scrape X.com trending page (works with proper headers)
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept-Language': 'th-TH,th;q=0.9',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            }
            
            # Try to get X.com explore/discover page
            url = 'https://x.com/explore'
            try:
                response = requests.get(url, headers=headers, timeout=10)
                response.encoding = 'utf-8'
                
                # Extract hashtags from page (look for #hashtag patterns)
                hashtag_pattern = r'#([ก-๙a-zA-Z0-9_]+)'
                matches = re.findall(hashtag_pattern, response.text)
                
                if matches:
                    # Count occurrences and get top 10
                    hashtag_counts = Counter(matches)
                    top_hashtags = hashtag_counts.most_common(10)
                    
                    hashtags = [
                        {
                            'hashtag': f'#{tag}',
                            'count': count,
                            'source': 'x_com_explore'
                        }
                        for tag, count in top_hashtags
                    ]
                    
                    logger.info(f"Found {len(hashtags)} trending hashtags from X.com")
                    return hashtags
            except Exception as e:
                logger.warning(f"Error scraping X.com directly: {e}")
            
            # Method 2: Fallback - use search API approach (curated Thai gossip/drama hashtags)
            thai_drama_hashtags = [
                'ดราม่า', 'ดารา', 'อินฟลูเอนเซอร์', 'ไวรัล', 
                'ข่าวสาร', 'เท่าไหร่', 'จริงจังไหม', 'กระทะ',
                'ปะทะ', 'ทะเลาะ', 'ทะเบ', 'ตัดสิน'
            ]
            
            # Simulate trending by searching each hashtag's tweet count
            for tag in thai_drama_hashtags:
                try:
                    search_url = f'https://x.com/search?q=%23{tag}&f=live'
                    response = requests.get(search_url, headers=headers, timeout=8)
                    
                    # Count mentions (simple heuristic)
                    mentions = response.text.count(tag)
                    
                    hashtags.append({
                        'hashtag': f'#{tag}',
                        'count': mentions,
                        'source': 'x_com_search'
                    })
                except:
                    pass
                
                time.sleep(0.5)  # Rate limiting
            
            # Sort by count and get top 10
            hashtags = sorted(hashtags, key=lambda x: x['count'], reverse=True)[:10]
            
            return hashtags
            
        except Exception as e:
            logger.error(f"Error scraping X.com trending hashtags: {e}")
            # Return backup hashtags if scraping fails
            return [
                {'hashtag': '#ดราม่า', 'count': 0, 'source': 'backup'},
                {'hashtag': '#ดารา', 'count': 0, 'source': 'backup'},
                {'hashtag': '#อินฟลูเอนเซอร์', 'count': 0, 'source': 'backup'},
                {'hashtag': '#ไวรัล', 'count': 0, 'source': 'backup'},
                {'hashtag': '#ข่าวสาร', 'count': 0, 'source': 'backup'},
                {'hashtag': '#ความรัก', 'count': 0, 'source': 'backup'},
                {'hashtag': '#ปะทะ', 'count': 0, 'source': 'backup'},
                {'hashtag': '#ทะเลาะ', 'count': 0, 'source': 'backup'},
                {'hashtag': '#ทะเบ', 'count': 0, 'source': 'backup'},
                {'hashtag': '#ตัดสิน', 'count': 0, 'source': 'backup'}
            ]

# ============ NLP & ANALYSIS ============
class NewsAnalyzer:
    def __init__(self):
        # Thai keywords organized by category (INCLUDING NEW GOSSIP/DRAMA CATEGORIES)
        self.keywords_th = {
            'politics': ['เลือกตั้ง', 'สภา', 'รัฐมนตรี', 'นายก', 'กฎหมาย', 'รัฐบาล', 'การเมือง', 'มติ', 'ประกาศ', 'พรรค'],
            'economy': ['ตลาด', 'เศรษฐกิจ', 'บาท', 'ราคา', 'ปล้อง', 'ธุรกิจ', 'ธนาคาร', 'การค้า', 'ส่งออก', 'นำเข้า', 'GDP', 'เงินเฟ้อ'],
            'stock_market': ['ตลาดหุ้น', 'SET', 'MAI', 'หุ้น', 'เทรด', 'ดัชนี', 'จ้าว', 'ลง', 'ขึ้น', 'ปล้อง', 'ซื้อขาย'],
            'technology': ['เทคโนโลยี', 'ไอที', 'AI', 'ดิจิทัล', 'เว็บ', 'แอป', 'โปรแกรม', 'ระบบ', 'อินเทอร์เน็ต', 'ซอฟต์แวร์'],
            'entertainment': ['บันเทิง', 'ละคร', 'ดาราชาย', 'ดาราหญิง', 'เพลง', 'ภาพยนตร์', 'ซีรี่ส์', 'ละครชั่วโมง', 'ดารา', 'ซุปเปอร์สตาร์'],
            'sports': ['ฟุตบอล', 'กีฬา', 'ทีม', 'นักกีฬา', 'แชมป์', 'ลีก', 'โอลิมปิก', 'วอลเลย์บอล', 'เทนนิส'],
            'health': ['สุขภาพ', 'โรค', 'แพทย์', 'โรงพยาบาล', 'ยา', 'ไวรัส', 'กระบาด', 'อาการ', 'วัคซีน', 'ผู้ป่วย'],
            'disaster': ['อุบัติเหตุ', 'ไฟ', 'น้ำ', 'เหตุการณ์', 'ภัย', 'ฉุกเฉิน', 'พายุ', 'แผ่นดินไหว', 'ครอบครัว'],
            'social': ['สังคม', 'ชุมชน', 'สิทธิ', 'ผู้คน', 'การเดินขบวน', 'ประท้วง', 'ปัญหา', 'ความเห็น'],
            'travel': ['ท่องเที่ยว', 'เที่ยว', 'ตลาดท่องเที่ยว', 'นักท่องเที่ยว', 'ด่านศุลกากร', 'สถานที่', 'ทัศนะ', 'รีสอร์ท'],
            'crime': ['อาชญากรรม', 'จับกุม', 'ตำรวจ', 'คดีความ', 'ศาล', 'ประหาร', 'จำนวน', 'แก๊ง', 'ยา', 'ยืมเงิน'],
            # NEW CATEGORIES: GOSSIP & DRAMA
            'gossip': ['ข่าวสาร', 'บ้านเมือง', 'ลืออ', 'บอก', 'ได้ยิน', 'คนบอก', 'เล่าให้ฟัง', 'พูดถึง', 'บอกกับ', 'หลุด', 'เก้ง', 'ชี้', 'ว่าว่า', 'พูดจาง', 'ข่าวลือ'],
            'online_drama': ['ดราม่า', 'ออนไลน์', 'ปะทะ', 'ทะเลาะ', 'ทะเบียบ', 'แปลง', 'กร้าวๆ', 'บ่นด่า', 'เอากัน', 'ตัดสิน', 'ฟ้องร้อง', 'ทะเบ', 'ขัดแย้ง', 'ตัวตั้งตัวจริง'],
            'influencer': ['อินฟลูเอนเซอร์', 'ยูทูบเบอร์', 'ทิกต็อกเกอร์', 'อินสตาแกรมเมอร์', 'ครีเอเตอร์', 'บล็อกเกอร์', 'สตรีมเมอร์', 'แนวโน้ม', 'ไวรัล', 'ศิลปิน', 'กูรู'],
            'celebrity_love': ['รักษ์', 'ความรัก', 'แพ้ใจ', 'หลุดรัก', 'อินฟูล', 'เรื่องเจ้าสาว', 'เรื่องชายหนุ่ม', 'จีบ', 'หวง', 'คนรัก', 'แฟน', 'ผัว', 'เมีย', 'ไลเก่น', 'ผู้ชายและผู้หญิง', 'ด้านไลเก่น'],
            'celebrity_gossip': ['ข่าวดารา', 'ดาราตัวจริง', 'เรื่องเด็ก', 'ครอบครัว', 'บ้านดารา', 'รถดารา', 'แฟชั่นดารา', 'ร้านอาหารดารา', 'ชีวิตส่วนตัว', 'ลักษณ์นอก'],
        }
        
        # English keywords (for mixed content)
        self.keywords_en = {
            'technology': ['AI', 'tech', 'digital', 'software', 'app', 'internet', 'startup', 'innovation'],
            'economy': ['market', 'economy', 'trade', 'business', 'investment', 'price', 'export'],
            'cryptocurrency': ['bitcoin', 'crypto', 'ethereum', 'blockchain', 'NFT', 'coin'],
        }
    
    def extract_keywords(self, text):
        """Extract Thai and English keywords from text"""
        found_keywords = {}
        text_lower = text.lower()
        
        # Extract Thai keywords
        for category, keywords in self.keywords_th.items():
            matches = [kw for kw in keywords if kw in text_lower]
            if matches:
                found_keywords[category] = matches
        
        # Extract English keywords if no Thai keywords found
        if not found_keywords:
            for category, keywords in self.keywords_en.items():
                matches = [kw for kw in keywords if kw in text_lower]
                if matches:
                    found_keywords[category] = matches
        
        return found_keywords
    
    def get_viral_score(self, title, keywords_dict):
        """Calculate virality score (0-100)"""
        score = 0
        
        # Capital letters/excitement indicators (Thai)
        capitals = sum(1 for c in title if c.isupper())
        score += min(capitals * 5, 20)
        
        # Keywords found
        score += len(keywords_dict) * 10
        
        # Key phrases that attract attention
        viral_phrases = ['ด่วน', 'สำคัญ', 'เสียสละ', 'เพื่อการ', 'ข้อมูล', 'เปิดเผย', 'ลือ']
        for phrase in viral_phrases:
            if phrase in title.lower():
                score += 15
        
        # Length (optimal length = more clicks)
        title_length = len(title)
        if 30 <= title_length <= 80:
            score += 15
        
        return min(score, 100)
    
    def generate_summary(self, title, snippet):
        """Generate concise summary"""
        if snippet:
            return snippet[:150] + "..." if len(snippet) > 150 else snippet
        return title[:100]
    
    def extract_named_entities(self, text):
        """Extract people, places, organizations, numbers from text"""
        entities = {
            'people': [],
            'places': [],
            'organizations': [],
            'numbers': [],
            'percentages': [],
            'dates': []
        }
        
        # Thai titles/honorifics for people detection
        thai_titles = ['นาย', 'นาง', 'นางสาว', 'คุณ', 'ศาสตราจารย์', 'ดร.', 'นายก', 'รัฐมนตรี', 'ผู้ว่า', 'ศบค.']
        thai_places = ['กรุงเทพ', 'เชียงใหม่', 'ประเทศไทย', 'จังหวัด', 'กรม', 'สำนักงาน', 'โรงแรม', 'สนามบิน', 'สถานี', 'วังไทย']
        thai_orgs = ['ธนาคาร', 'บริษัท', 'กระทรวง', 'สภา', 'องค์กร', 'หน่วยงาน', 'สถาบัน', 'คณะ', 'มหาวิทยาลัย', 'สหประชาชาติ', 'เอกชน', 'รัฐวิสาหกิจ']
        
        # Extract numbers
        numbers = re.findall(r'\d+(?:,\d{3})*(?:\.\d+)?', text)
        entities['numbers'] = sorted(list(set(numbers)), key=lambda x: -int(x.replace(',', '')))[:10]
        
        # Extract percentages
        percentages = re.findall(r'\d+(?:\.\d+)?%', text)
        entities['percentages'] = list(set(percentages))[:10]
        
        # Extract dates (Thai and Western formats)
        dates = re.findall(r'\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{1,2}-\d{1,2}', text)
        entities['dates'] = list(set(dates))[:10]
        
        # Extract people (after Thai titles and English names)
        for title in thai_titles:
            pattern = f'{title}\\s+([ก-๙a-zA-Z]+(?:\\s+[ก-๙a-zA-Z]+)?)'
            matches = re.findall(pattern, text)
            entities['people'].extend(matches)
        
        # Look for common English names (Full Name format)
        english_names = re.findall(r'\b[A-Z][a-z]+\s+[A-Z][a-z]+\b', text)
        entities['people'].extend(english_names)
        
        # Extract places (Thai provinces and location keywords)
        for place_word in thai_places:
            if place_word in text:
                # Get word before or after place keyword
                pattern = f'([ก-๙a-zA-Z]{2,}\\s*)?{place_word}|{place_word}\\s+([ก-๙a-zA-Z]{2,})?'
                matches = re.findall(pattern, text)
                for match in matches:
                    if isinstance(match, tuple):
                        for m in match:
                            if m.strip():
                                entities['places'].append(m.strip())
                    else:
                        if match.strip():
                            entities['places'].append(match.strip())
        
        # Extract organizations
        for org_word in thai_orgs:
            if org_word in text:
                pattern = f'([ก-๙a-zA-Z\\s]{2,}){org_word}'
                matches = re.findall(pattern, text)
                entities['organizations'].extend(matches)
        
        # Deduplicate and limit
        for key in entities:
            if isinstance(entities[key], list):
                entities[key] = list(set([str(e).strip() for e in entities[key] if str(e).strip()]))[:15]
        
        return entities
    
    def highlight_deep_dive_info(self, text, keywords_dict):
        """Extract investigation angles from article with named entities and questions"""
        highlights = {
            'main_topics': [],
            'entities': [],
            'named_entities': {},
            'questions_to_explore': []
        }
        
        # Extract categories
        highlights['main_topics'] = list(keywords_dict.keys())
        
        # Extract named entities
        highlights['named_entities'] = self.extract_named_entities(text)
        
        # Find numbers (potential data points)
        numbers = re.findall(r'\d+(?:[,,.]\d+)*', text)
        highlights['entities'] = numbers[:5] if numbers else []
        
        # Generate investigation questions in Thai and English
        questions = {
            'politics': ['🏛️ ผลกระทบต่อการเมือง?', '🏛️ Political implications?', '👥 ใครเกี่ยวข้องบ้าง?'],
            'economy': ['📊 ผลกระทบต่อเศรษฐกิจ?', '📊 Economic impact?', '💱 ราคาจะเปลี่ยนไป?'],
            'stock_market': ['📈 SET ขึ้นหรือลง?', '📈 SET UP or DOWN?', '🏦 หมวดไหนได้รับผลกระทบ?'],
            'technology': ['💻 เทคโนโลยีใหม่?', '💻 Tech innovation?', '🚀 มีผลกระทบต่อตลาดไทย?'],
            'health': ['⚕️ ผลกระทบต่อสุขภาพ?', '⚕️ Health impact?', '📋 วัคซีนหรือการรักษา?'],
            'disaster': ['🚨 ขนาดความเสียหาย?', '🚨 Impact scale?', '👨‍🚒 การตอบสนอง?'],
            'social': ['👥 ใครได้รับผลกระทบ?', '👥 Who is affected?', '⚖️ ความยุติธรรม?'],
            'travel': ['✈️ ผลต่อการท่องเที่ยว?', '✈️ Tourism impact?', '🏨 โรงแรมจะได้รับผล?'],
            'entertainment': ['🎬 ผลต่อวงการบันเทิง?', '🎬 Entertainment impact?', '⭐ ดาราคนไหนเกี่ยวข้อง?'],
            'crime': ['🚔 การสืบสวน?', '🚔 Investigation?', '⚖️ ลงโทษ?'],
            # NEW: GOSSIP, DRAMA, INFLUENCER, CELEBRITY CATEGORIES
            'gossip': ['🗨️ ข่าวลือจริงหรือไม่?', '🗨️ Is the gossip true?', '👥 ใครบอกข่าวลือนี้?'],
            'online_drama': ['⚡ ดราม่านี้มีชีวิตไหม?', '⚡ Is this drama trending?', '🔥 ใครกับใครกำลังทะเลาะ?'],
            'influencer': ['🌟 อินฟูเอนเซอร์ใครเกี่ยวข้อง?', '🌟 Which influencer involved?', '📱 เพื่อนหรือศัตรูของใคร?'],
            'celebrity_love': ['💕 จริงอินฟูลหรือลือเท่านั้น?', '💕 Real or just rumor?', '💔 หักดิบแล้วหรือยัง?'],
            'celebrity_gossip': ['⭐ ข่าวตัวจริงหรือแต่งเรื่อง?', '⭐ Fact or fabrication?', '📸 มีหลักฐานหรือเพียงเล่าเรื่อง?']
        }
        
        for topic in highlights['main_topics']:
            if topic in questions:
                highlights['questions_to_explore'].extend(questions[topic][:2])
        
        return highlights
    
    def analyze_article(self, article):
        """Complete analysis of a single article"""
        keywords = self.extract_keywords(article['title'] + ' ' + article['snippet'])
        viral_score = self.get_viral_score(article['title'], keywords)
        summary = self.generate_summary(article['title'], article['snippet'])
        deep_dive = self.highlight_deep_dive_info(
            article['title'] + ' ' + article['snippet'], 
            keywords
        )
        
        # Determine primary category
        primary_category = list(keywords.keys())[0] if keywords else 'general'
        
        return {
            **article,
            'keywords': json.dumps(keywords),
            'viral_score': viral_score,
            'summary': summary,
            'category': primary_category,
            'deep_dive_highlights': json.dumps(deep_dive),
            'sentiment_score': 0.5  # Placeholder
        }

# ============ TREND DETECTION ============
class TrendDetector:
    def detect_trending_topics(self, articles):
        """Detect trending topics from articles"""
        all_keywords = []
        keyword_articles = {}
        
        for article in articles:
            keywords = json.loads(article['keywords'])
            for category in keywords:
                all_keywords.append(category)
                if category not in keyword_articles:
                    keyword_articles[category] = []
                keyword_articles[category].append(article)
        
        # Count frequency
        keyword_counts = Counter(all_keywords)
        trending = keyword_counts.most_common(10)
        
        trends = []
        for topic, count in trending:
            related_articles = keyword_articles.get(topic, [])
            avg_viral = sum(a['viral_score'] for a in related_articles) / len(related_articles)
            
            investigation_angles = {
                'source_diversity': len(set(a['source'] for a in related_articles)),
                'top_articles': [a['title'][:80] for a in sorted(
                    related_articles, 
                    key=lambda x: x['viral_score'], 
                    reverse=True
                )[:3]],
                'suggested_deep_dive': f"Analyze {count} articles across {len(set(a['source'] for a in related_articles))} sources"
            }
            
            trends.append({
                'topic': topic,
                'frequency': count,
                'viral_score_avg': round(avg_viral, 2),
                'articles_count': len(related_articles),
                'investigation_angles': investigation_angles
            })
        
        return trends

# ============ DATABASE OPERATIONS ============
def save_articles_to_db(articles):
    """Save analyzed articles to database"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    for article in articles:
        try:
            c.execute('''INSERT OR IGNORE INTO news 
                        (title, summary, source, url, keywords, viral_score, 
                         category, deep_dive_highlights)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                     (article['title'], article['summary'], article['source'],
                      article['url'], article['keywords'], article['viral_score'],
                      article['category'], article['deep_dive_highlights']))
        except Exception as e:
            logger.error(f"Error saving article: {e}")
    
    conn.commit()
    conn.close()

def save_trends_to_db(trends):
    """Save trending topics to database"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    for trend in trends:
        c.execute('''INSERT OR REPLACE INTO trending_topics 
                    (topic, frequency, sentiment_avg, articles_count, 
                     investigation_angles, last_updated)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)''',
                 (trend['topic'], trend['frequency'], trend['viral_score_avg'],
                  trend['articles_count'], json.dumps(trend['investigation_angles'])))
    
    conn.commit()
    conn.close()

def get_top_virals_by_category(limit=5):
    """Get top 5 most viral articles from each category"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Get all categories
    c.execute('SELECT DISTINCT category FROM news WHERE category IS NOT NULL')
    categories = [row[0] for row in c.fetchall()]
    
    top_virals = {}
    
    for category in categories:
        # Get top N viral articles for this category
        c.execute('''SELECT id, title, source, url, viral_score, category, published_date, summary
                     FROM news 
                     WHERE category = ? 
                     ORDER BY viral_score DESC 
                     LIMIT ?''', (category, limit))
        
        columns = ['id', 'title', 'source', 'url', 'viral_score', 'category', 'published_date', 'summary']
        articles = [dict(zip(columns, row)) for row in c.fetchall()]
        
        if articles:
            top_virals[category] = articles
    
    conn.close()
    return top_virals

def get_overall_top_virals(limit=50):
    """Get overall top viral articles across all categories"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''SELECT id, title, source, url, viral_score, category, published_date, summary
                 FROM news 
                 ORDER BY viral_score DESC 
                 LIMIT ?''', (limit,))
    
    columns = ['id', 'title', 'source', 'url', 'viral_score', 'category', 'published_date', 'summary']
    articles = [dict(zip(columns, row)) for row in c.fetchall()]
    
    conn.close()
    return articles
    """Retrieve articles from database with optional timeframe filter"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    query = 'SELECT * FROM news WHERE viral_score >= ?'
    params = [min_viral_score]
    
    if category:
        query += ' AND category = ?'
        params.append(category)
    
    # Add timeframe filter
    if timeframe:
        start_date = get_date_from_timeframe(timeframe)
        if start_date:
            query += ' AND published_date >= ?'
            params.append(start_date.isoformat())
    
    query += ' ORDER BY viral_score DESC, published_date DESC LIMIT ?'
    params.append(limit)
    
    c.execute(query, params)
    columns = [desc[0] for desc in c.description]
    articles = [dict(zip(columns, row)) for row in c.fetchall()]
    
    conn.close()
    return articles

def get_date_from_timeframe(timeframe):
    """Convert timeframe string to start date"""
    timeframe_map = {
        '24h': timedelta(hours=24),
        '3d': timedelta(days=3),
        '7d': timedelta(days=7),
        '14d': timedelta(days=14),
        '1m': timedelta(days=30),
        '3m': timedelta(days=90),
        '6m': timedelta(days=180),
        '1y': timedelta(days=365),
        'all': None  # No limit
    }
    
    if timeframe not in timeframe_map:
        return None
    
    delta = timeframe_map[timeframe]
    if delta is None:
        return None
    
    return datetime.now() - delta

def get_trending_topics_from_db(limit=20):
    """Retrieve trending topics"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''SELECT topic, frequency, sentiment_avg, articles_count, 
                 investigation_angles, last_updated 
                 FROM trending_topics 
                 ORDER BY frequency DESC LIMIT ?''', (limit,))
    
    columns = [desc[0] for desc in c.description]
    trends = [dict(zip(columns, row)) for row in c.fetchall()]
    
    conn.close()
    return trends

# ============ BACKGROUND SCRAPING ============
def background_scraper():
    """Run scraper periodically in background"""
    scraper = NewsScraperThai()
    analyzer = NewsAnalyzer()
    detector = TrendDetector()
    
    while True:
        try:
            logger.info("Starting news scrape...")
            
            # Scrape articles
            raw_articles = scraper.scrape_all()
            
            # Analyze articles
            analyzed_articles = [analyzer.analyze_article(a) for a in raw_articles]
            
            # Save to database
            save_articles_to_db(analyzed_articles)
            
            # Detect trends
            trends = detector.detect_trending_topics(analyzed_articles)
            save_trends_to_db(trends)
            
            logger.info(f"Scrape complete: {len(analyzed_articles)} articles, {len(trends)} trends")
            
            # Wait 1 hour before next scrape
            import time
            time.sleep(3600)
        except Exception as e:
            logger.error(f"Background scraper error: {e}")
            import time
            time.sleep(300)  # Retry after 5 minutes

# ============ API ROUTES ============
@app.route('/api/articles', methods=['GET'])
def get_articles():
    """Get articles with filters including timeframe"""
    limit = request.args.get('limit', 50, type=int)
    category = request.args.get('category', None)
    min_viral = request.args.get('min_viral_score', 0, type=float)
    timeframe = request.args.get('timeframe', 'all')  # 24h, 3d, 7d, 14d, 1m, 3m, 6m, 1y, all
    
    articles = get_articles_from_db(limit, category, min_viral, timeframe)
    
    # Parse JSON fields
    for article in articles:
        try:
            article['keywords'] = json.loads(article['keywords']) if article['keywords'] else {}
            article['deep_dive_highlights'] = json.loads(article['deep_dive_highlights']) if article['deep_dive_highlights'] else {}
        except:
            pass
    
    return jsonify({
        'success': True,
        'count': len(articles),
        'timeframe': timeframe,
        'articles': articles
    })

@app.route('/api/trending', methods=['GET'])
def get_trending():
    """Get trending topics"""
    limit = request.args.get('limit', 20, type=int)
    trends = get_trending_topics_from_db(limit)
    
    for trend in trends:
        try:
            trend['investigation_angles'] = json.loads(trend['investigation_angles']) if trend['investigation_angles'] else {}
        except:
            pass
    
    return jsonify({
        'success': True,
        'trends': trends
    })

@app.route('/api/scrape-now', methods=['POST'])
def scrape_now():
    """Trigger immediate scrape (admin endpoint)"""
    try:
        scraper = NewsScraperThai()
        analyzer = NewsAnalyzer()
        detector = TrendDetector()
        
        raw_articles = scraper.scrape_all()
        analyzed_articles = [analyzer.analyze_article(a) for a in raw_articles]
        save_articles_to_db(analyzed_articles)
        
        trends = detector.detect_trending_topics(analyzed_articles)
        save_trends_to_db(trends)
        
        return jsonify({
            'success': True,
            'articles_scraped': len(analyzed_articles),
            'trends_detected': len(trends)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/investigation', methods=['POST'])
def create_investigation():
    """Create investigation log for deep dive"""
    data = request.json
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''INSERT INTO investigation_logs 
                (topic, angle, findings, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)''',
             (data['topic'], data['angle'], data.get('findings', ''), 'active'))
    
    conn.commit()
    investigation_id = c.lastrowid
    conn.close()
    
    return jsonify({
        'success': True,
        'investigation_id': investigation_id
    })

@app.route('/api/timeframe-stats', methods=['GET'])
def timeframe_stats():
    """Get statistics for different timeframes"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    stats = {}
    timeframes = {
        '24h': (datetime.now() - timedelta(hours=24), '24 Hours'),
        '3d': (datetime.now() - timedelta(days=3), '3 Days'),
        '7d': (datetime.now() - timedelta(days=7), '7 Days'),
        '14d': (datetime.now() - timedelta(days=14), '14 Days'),
        '1m': (datetime.now() - timedelta(days=30), '1 Month'),
        '3m': (datetime.now() - timedelta(days=90), '3 Months'),
        'all': (None, 'All Time')
    }
    
    for tf_key, (start_date, label) in timeframes.items():
        if start_date:
            c.execute(
                'SELECT COUNT(*) as count, AVG(viral_score) as avg_viral FROM news WHERE published_date >= ?',
                (start_date.isoformat(),)
            )
        else:
            c.execute('SELECT COUNT(*) as count, AVG(viral_score) as avg_viral FROM news')
        
        result = c.fetchone()
        stats[tf_key] = {
            'label': label,
            'count': result[0] or 0,
            'avg_viral_score': round(result[1], 1) if result[1] else 0,
            'start_date': start_date.isoformat() if start_date else None
        }
    
    conn.close()
    
    return jsonify({
        'success': True,
        'timeframe_stats': stats,
        'current_time': datetime.now().isoformat()
    })

@app.route('/api/health', methods=['GET'])
def health():
    """Health check"""
    return jsonify({'status': 'running', 'timestamp': datetime.now().isoformat()})

@app.route('/api/entities', methods=['GET'])
def get_entities():
    """Get all unique entities (people, places, organizations, etc.)"""
    entity_type = request.args.get('type', 'all')  # people, places, organizations, numbers, all
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Get all articles with their deep dive highlights
    c.execute('SELECT deep_dive_highlights FROM news WHERE deep_dive_highlights IS NOT NULL')
    rows = c.fetchall()
    
    all_entities = {
        'people': {},
        'places': {},
        'organizations': {},
        'numbers': {},
        'percentages': {},
        'dates': {}
    }
    
    # Aggregate all entities
    for row in rows:
        try:
            highlights = json.loads(row[0])
            if 'named_entities' in highlights:
                entities = highlights['named_entities']
                for ent_type, values in entities.items():
                    if isinstance(values, list):
                        for value in values:
                            if value:
                                all_entities[ent_type][value] = all_entities[ent_type].get(value, 0) + 1
        except:
            pass
    
    conn.close()
    
    # Filter by type if requested
    if entity_type != 'all':
        filtered = {entity_type: all_entities.get(entity_type, {})}
    else:
        filtered = all_entities
    
    # Convert to sorted list by frequency
    result = {}
    for ent_type, entities_dict in filtered.items():
        result[ent_type] = sorted(
            [{'name': k, 'count': v} for k, v in entities_dict.items()],
            key=lambda x: -x['count']
        )[:50]  # Top 50 per type
    
    return jsonify({
        'success': True,
        'entities': result,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/articles-by-entity', methods=['GET'])
def get_articles_by_entity():
    """Get articles that mention a specific person, place, or organization"""
    entity_name = request.args.get('name', '')
    entity_type = request.args.get('type', 'people')  # people, places, organizations, etc.
    
    if not entity_name:
        return jsonify({'success': False, 'error': 'Entity name required'}), 400
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Get all articles
    c.execute('SELECT id, title, source, url, viral_score, category, deep_dive_highlights FROM news ORDER BY viral_score DESC LIMIT 100')
    columns = ['id', 'title', 'source', 'url', 'viral_score', 'category', 'deep_dive_highlights']
    rows = c.fetchall()
    
    matching_articles = []
    
    for row in rows:
        article = dict(zip(columns, row))
        try:
            highlights = json.loads(article['deep_dive_highlights'])
            if 'named_entities' in highlights:
                entities = highlights['named_entities']
                if entity_type in entities and entity_name in entities[entity_type]:
                    matching_articles.append(article)
        except:
            pass
    
    conn.close()
    
    return jsonify({
        'success': True,
        'entity_name': entity_name,
        'entity_type': entity_type,
        'count': len(matching_articles),
        'articles': matching_articles
    })

@app.route('/api/entity-network', methods=['GET'])
def entity_network():
    """Get network of related entities (who appears with whom, etc.)"""
    entity_name = request.args.get('name', '')
    
    if not entity_name:
        return jsonify({'success': False, 'error': 'Entity name required'}), 400
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('SELECT deep_dive_highlights FROM news')
    rows = c.fetchall()
    
    related_entities = {
        'people': {},
        'places': {},
        'organizations': {}
    }
    
    found_in_articles = 0
    
    for row in rows:
        try:
            highlights = json.loads(row[0])
            if 'named_entities' in highlights:
                entities = highlights['named_entities']
                
                # Check if this entity appears in any category
                found = False
                for ent_type in ['people', 'places', 'organizations']:
                    if entity_name in entities.get(ent_type, []):
                        found = True
                        break
                
                if found:
                    found_in_articles += 1
                    # Add related entities from the same article
                    for ent_type in ['people', 'places', 'organizations']:
                        for ent_name in entities.get(ent_type, []):
                            if ent_name != entity_name:
                                related_entities[ent_type][ent_name] = related_entities[ent_type].get(ent_name, 0) + 1
        except:
            pass
    
    conn.close()
    
    # Sort by frequency
    result = {}
    for ent_type, entities_dict in related_entities.items():
        result[ent_type] = sorted(
            [{'name': k, 'co_occurrence': v} for k, v in entities_dict.items()],
            key=lambda x: -x['co_occurrence']
        )[:20]
    
    return jsonify({
        'success': True,
        'entity_name': entity_name,
        'found_in_articles': found_in_articles,
        'related_entities': result
    })

@app.route('/api/top-virals-by-category', methods=['GET'])
def get_top_virals_endpoint():
    """Get top 5 most viral articles from each category"""
    limit = request.args.get('limit', 5, type=int)
    
    try:
        top_virals = get_top_virals_by_category(limit)
        
        # Parse JSON fields for each article
        for category in top_virals:
            for article in top_virals[category]:
                try:
                    if 'deep_dive_highlights' in article:
                        article['deep_dive_highlights'] = json.loads(article.get('deep_dive_highlights', '{}'))
                except:
                    pass
        
        return jsonify({
            'success': True,
            'categories': len(top_virals),
            'limit_per_category': limit,
            'top_virals': top_virals,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error getting top virals by category: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'top_virals': {}
        }), 500

@app.route('/api/overall-top-virals', methods=['GET'])
def get_overall_top_virals_endpoint():
    """Get overall top viral articles across all categories"""
    limit = request.args.get('limit', 50, type=int)
    
    try:
        articles = get_overall_top_virals(limit)
        
        # Parse JSON fields
        for article in articles:
            try:
                if 'deep_dive_highlights' in article:
                    article['deep_dive_highlights'] = json.loads(article.get('deep_dive_highlights', '{}'))
            except:
                pass
        
        return jsonify({
            'success': True,
            'count': len(articles),
            'limit': limit,
            'articles': articles,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error getting overall top virals: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'articles': []
        }), 500

@app.route('/api/top-virals-stats', methods=['GET'])
def get_top_virals_stats():
    """Get statistics about viral content across categories"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        stats = {}
        
        # Get stats for each category
        c.execute('''SELECT category, 
                            COUNT(*) as total_articles,
                            AVG(viral_score) as avg_viral_score,
                            MAX(viral_score) as max_viral_score,
                            MIN(viral_score) as min_viral_score
                     FROM news 
                     WHERE category IS NOT NULL
                     GROUP BY category
                     ORDER BY avg_viral_score DESC''')
        
        for row in c.fetchall():
            category, total, avg_score, max_score, min_score = row
            stats[category] = {
                'total_articles': total,
                'avg_viral_score': round(avg_score, 1) if avg_score else 0,
                'max_viral_score': max_score,
                'min_viral_score': min_score
            }
        
        conn.close()
        
        return jsonify({
            'success': True,
            'stats': stats,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error getting viral stats: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'stats': {}
        }), 500


def get_x_trending_hashtags():
    """Get top 10 trending hashtags from X.com"""
    try:
        scraper = NewsScraperThai()
        hashtags = scraper.scrape_x_trending_hashtags()
        
        # Save to database
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Clear old hashtags (keep only latest)
        c.execute('DELETE FROM x_trending_hashtags WHERE scraped_at < datetime("now", "-1 hour")')
        
        # Insert new hashtags
        for position, hashtag_data in enumerate(hashtags, 1):
            try:
                c.execute('''INSERT INTO x_trending_hashtags 
                           (hashtag, count, source, position)
                           VALUES (?, ?, ?, ?)''',
                         (hashtag_data['hashtag'], hashtag_data['count'], 
                          hashtag_data['source'], position))
            except:
                pass
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'count': len(hashtags),
            'hashtags': hashtags,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error getting X trending hashtags: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'hashtags': []
        }), 500

@app.route('/api/articles-by-hashtag', methods=['GET'])
def get_articles_by_hashtag():
    """Get articles that mention a specific hashtag from X.com trends"""
    hashtag = request.args.get('hashtag', '').lstrip('#')
    category_filter = request.args.get('category', 'all')
    
    if not hashtag:
        return jsonify({'success': False, 'error': 'Hashtag required'}), 400
    
    try:
        scraper = NewsScraperThai()
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept-Language': 'th-TH,th;q=0.9',
        }
        
        # Search for hashtag on X.com
        search_url = f'https://x.com/search?q=%23{hashtag}&f=live'
        response = requests.get(search_url, headers=headers, timeout=10)
        response.encoding = 'utf-8'
        
        # Extract tweets/articles mentioning hashtag
        articles = []
        
        # Simple extraction - look for tweet-like patterns
        tweet_pattern = r'<div[^>]*class="[^"]*tweet[^"]*"[^>]*>(.+?)</div>'
        matches = re.findall(tweet_pattern, response.text, re.DOTALL)
        
        for match in matches[:20]:  # Get up to 20 results
            # Extract title/text
            text_match = re.search(r'>([^<]{20,200})</span>', match)
            if text_match:
                title = text_match.group(1).strip()
                
                # Extract engagement count (retweets, likes)
                count_match = re.search(r'(\d+)\s*(?:Retweets|Likes|Replies)', match)
                engagement = int(count_match.group(1)) if count_match else 0
                
                articles.append({
                    'title': title,
                    'hashtag': f'#{hashtag}',
                    'engagement': engagement,
                    'source': 'x_com',
                    'url': f'https://x.com/search?q=%23{hashtag}'
                })
        
        # If no articles found, return empty list
        if not articles:
            articles = []
        
        return jsonify({
            'success': True,
            'hashtag': f'#{hashtag}',
            'count': len(articles),
            'articles': articles,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error getting articles by hashtag: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'articles': []
        }), 500

@app.route('/api/hashtag-history', methods=['GET'])
def get_hashtag_history():
    """Get hashtag trending history over time"""
    hashtag = request.args.get('hashtag', '').lstrip('#')
    days = request.args.get('days', 7, type=int)
    
    if not hashtag:
        return jsonify({'success': False, 'error': 'Hashtag required'}), 400
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Get history for hashtag
        start_date = (datetime.now() - timedelta(days=days)).date()
        c.execute('''SELECT date, rank, count FROM hashtag_history 
                     WHERE hashtag = ? AND date >= ? 
                     ORDER BY date ASC''',
                 (f'#{hashtag}', start_date))
        
        rows = c.fetchall()
        conn.close()
        
        history = [
            {
                'date': row[0],
                'rank': row[1],
                'count': row[2]
            }
            for row in rows
        ]
        
        return jsonify({
            'success': True,
            'hashtag': f'#{hashtag}',
            'days': days,
            'history': history,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error getting hashtag history: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'history': []
        }), 500



if __name__ == '__main__':
    # Start background scraper in separate thread
    scraper_thread = Thread(target=background_scraper, daemon=True)
    scraper_thread.start()
    
    # Run Flask app
    app.run(debug=False, host='0.0.0.0', port=5000)
