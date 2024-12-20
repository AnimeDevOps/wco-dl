import re
import json
import pathlib
import argparse
import concurrent.futures

import pydantic
import requests
import tqdm
import bs4
import pick

class Config_Model(pydantic.BaseModel):
    version: str = '0.0.1'
    quality: str = '1080p'
    pretty: bool = True
    quality_in_filename: bool = True
    resume_download: bool = True
    config_directory: str = f'{str(pathlib.Path.home())}/.config/wco'
    download_directory: str = './downloads'

class Config:
    config: Config_Model = Config_Model() 
    def __init__(self) -> None:
        config_file = pathlib.Path(f'{self.config.config_directory}/config.json')
        if (not config_file.is_file()):
            pathlib.Path(self.config.config_directory).mkdir(parents=True, exist_ok=True)
            self.config = Config_Model()
            config_file.write_text(self.config.model_dump_json())
        self.config = pydantic.TypeAdapter(Config_Model).validate_json(config_file.read_text())

class Database:
    def add_anime_to_database(self, show_name: str, database_path: str):
        file_handel = pathlib.Path(database_path)
        content = {} if not file_handel.is_file() else  json.loads(file_handel.read_text())
        if (show_name in content):
            return
        content[show_name] = []
        file_handel.write_text(json.dumps(content))

    def add_episode_to_database(self, show_name:str, url: str, database_path: str):
        file_handel = pathlib.Path(database_path)
        content = json.loads(file_handel.read_text())
        if (url not in content[show_name]): 
            content[show_name].append(url)
            file_handel.write_text(json.dumps(content))

    def get_all_downloads(self, database_path: str):
        file_handel = pathlib.Path(database_path)
        content = json.loads(file_handel.read_text())
        return content

class Network:
    session = requests.Session()
    def raw_get(self, url: str, headers: dict={}) -> requests.models.Response:
        response: requests.models.Response = self.session.get(url, headers=headers) 
        if (response.ok):
            return response
        raise requests.exceptions.HTTPError('Response was not ok')

    def get(self, url: str, headers: dict={}) -> str:
        response: str = self.raw_get(url, headers).text
        return response

    def raw_post(self, url: str, headers: dict={}, data: dict={}) -> requests.models.Response:
        response: requests.models.Response = self.session.post(url, headers=headers, data=data) 
        if (response.ok):
            return response
        raise requests.exceptions.HTTPError('Response was not ok')

    def post(self, url: str, headers: dict={}, data: dict={}) -> str:
        response: str = self.raw_post(url, headers, data).text
        return response

    def download_file(self, label: str, url: str, headers: dict, filename: str, folder: str, resume_download: bool) -> str:
        file_size: int = 0 
        file_handel: pathlib.Path = pathlib.Path(f'{folder}/{filename}')
        pathlib.Path(folder).mkdir(parents=True, exist_ok=True)
        info: requests.models.Response = self.session.get(url, headers=headers, stream=True)
        if (file_handel.is_file()):
            file_size = file_handel.stat().st_size
            if (resume_download):
                headers.update({'Range': f'bytes={file_size}-'})
        if (file_size == int(info.headers['Content-Length'])):
            print(f'Skipping: {folder}/{filename}')
            return filename
        response: requests.models.Response = self.session.get(url, headers=headers, stream=True)
        with tqdm.tqdm(total=int(info.headers['Content-Length']), unit='B', unit_scale=True, unit_divisor=1024, desc=label, initial=file_size) as progress_bar:
            with open(file_handel, 'ab') as file_:
                for chunk in response.iter_content(1024):
                    file_.write(chunk)
                    progress_bar.update(len(chunk))
        return filename

class Scraper:
    configuration: Config_Model = Config().config 
    network_manager: Network = Network()
    def request(self, url: str, extra_headers: dict={}):
        headers: dict = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml,application/json;q=0.9,image/webp,*""" /*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
            'DNT': '1'
        }
        if extra_headers:
            headers.update(extra_headers)
        response: str = self.network_manager.get(url, headers)
        return response

    def select_resolution(self, sources: list[dict[str, str]]) -> dict: 
        for source in sources:
            if (source['label'] == self.configuration.quality):
                return source
        return sources[-1]

    def get_episodes(self, url: str) -> list[str]:
        page_source: str = self.request(url)
        soup: bs4.BeautifulSoup = bs4.BeautifulSoup(page_source, 'html.parser')
        episodes: list[str] = [link['href'] for link in soup.findAll('a', {'class': 'sonra'})]
        return episodes
    
    def get_hidden_url(self, url: str) -> str:
        page_source: str = self.request(url)
        soup: bs4.BeautifulSoup = bs4.BeautifulSoup(page_source, 'html.parser')
        iframe = soup.find('iframe', {'id', 'frameNewcizgifilmuploads0'})
        if (iframe is None):
            iframe = soup.find('iframe')
        if (iframe is None):
            if ('Become a Premium User Now!' in page_source):
                raise Exception('Premium episode cannot download')
            raise Exception('Unable to find iframe')
        return iframe['src']

    def get_sources(self, url: str, embed_url: str) -> list[dict]:
        page_source: str = self.network_manager.get(embed_url, headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36', 'Accept': '*/*', 'Referer': url, 'X-Requested-With': 'XMLHttpRequest'})
        if 'getvid?evid' in page_source:
            source_urls = []
            source_url: str = re.search(r'getJSON\("(.*?)"', page_source, re.DOTALL).group(1)
            page2_source = self.request(f'https://wcostream.tv{source_url}',
                extra_headers={
                    'Accept': '*/*', 'Referer': embed_url,
                    'X-Requested-With': 'XMLHttpRequest'
                }
            )
            json_data = json.loads(page2_source) 
            sd_token = json_data.get('enc', '')
            hd_token = json_data.get('hd', '')
            fhd_token = json_data.get('fhd', '')
            source_base_url = json_data.get('server', '') + '/getvid?evid='
            if sd_token:
                source_urls.append({'label': '480p', 'url': source_base_url + sd_token})  # Order the items as (LABEL, URL).
            if hd_token:
                source_urls.append({'label': '720p', 'url': source_base_url + hd_token})  # Order the items as (LABEL, URL).
            if fhd_token:
                source_urls.append({'label': '1080p', 'url': source_base_url + fhd_token})  # Order the items as (LABEL, URL).
            backup_url = json_data.get('cdn', '') + '/getvid?evid=' + (sd_token or hd_token)
        else:
            sources_block = re.search(r'sources:\s*?\[(.*?)\]', page_source, re.DOTALL).group(1)
            stream_pattern = re.compile(r'\{\s*?file:\s*?"(.*?)"(?:,\s*?label:\s*?"(.*?)")?')
            source_urls = [{'label': sourceMatch.group(2), 'url': sourceMatch.group(1)} for sourceMatch in stream_pattern.finditer(sources_block)]
            backup_match = stream_pattern.search(page_source[page_source.find(b'jw.onError'):])
            backup_url = backup_match.group(1) if backup_match else ''
        return source_urls, backup_url

    def search(self, query: str) -> list[str]:
        headers: dict = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36',}
        response: str = self.network_manager.post(f'https://www.wcostream.tv/search', headers, {'catara': query, 'konuara': 'series'})
        soup: bs4.BeautifulSoup = bs4.BeautifulSoup(response, 'html.parser')
        divs = soup.findAll('div', attrs={'class': 'left', 'id': 'blog'}) 
        results = []
        for div in divs:
            links = div.findAll('a')
            for a in links:
                results.append(a['href'])
        return results

    def info_extractor(self, url):
        url = re.sub('https://www.wcostream.tv/', '', url)
        try:
            if "season" in url:
                show_name, season, episode, desc = re.findall(r'([a-zA-Z0-9].+)\s(season\s\d+\s?)(episode\s\d+\s)?(.+)',
                                                              url.replace('-', ' '))[0]
            else:
                show_name, episode, desc = re.findall(r'([a-zA-Z0-9].+)\s(episode\s\d+\s?)(.+)', url.replace(
                    '-', ' '))[0]
                season = "season 1"
        except:
            show_name = url
            season = "Season 1"
            episode = "Episode 0"
            desc = ""
        return show_name.title().strip(), season.title().strip(), episode.title().strip(), desc.title().strip(), url

    def get_media_urls(self, url: str) -> tuple:
        embed_url = Scraper().get_hidden_url(url)
        sources, backup = Scraper().get_sources(url, embed_url)
        selected_source = self.select_resolution(sources)
        return selected_source['label'], selected_source['url'], backup, embed_url

def download_episode(url: str, network_manager: Network, scraper_manager: Scraper, config: Config_Model):
    resolution, media, backup, hidden_url = scraper_manager.get_media_urls(url)
    show_name, season, episode, desc, url = scraper_manager.info_extractor(url)
    header = {
        'Host': media.split("//")[-1].split("/")[0].split('?')[0],
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36',
        'Accept': 'video/webm,video/ogg,video/*;q=0.9,application/ogg;q=0.7,audio/*;q=0.6,*/*;q=0.5',
        'Accept-Language': 'en-US,en;q=0.5',                                                                                                                      
        'Connection': 'keep-alive',
        'Referer': hidden_url.replace('https://wcostream.tv', 'https://www.wcostream.tv'),
    }
    if (config.pretty):
        # TODO fix special downloads
        #if (episode == 'Episode 0' and season == 'Season 1'):
        #    season = 'Special'
        #    #episode = episode.replace('0', f"{str(next(os.walk(f'{scraper_manager.configuration.download_directory}/{show_name}/{season}'))[2] + 1)}")
        episode = f'{episode} {resolution}.mp4' if config.quality_in_filename else f'{episode}.mp4'
        return network_manager.download_file(f'Downloading: {show_name} {season} {episode}', media, header, f'{episode}', f'{scraper_manager.configuration.download_directory}/{show_name}/{season}', config.resume_download)
    else:
        return network_manager.download_file(f'Downloading: {show_name} {season} {episode}', media, header, f'{url.split('/')[-1]}.mp4', f'{Scraper().configuration.download_directory}/{show_name}', config.resume_download)

def args_parser():
    parser = argparse.ArgumentParser(description='Download wcostream content')
    parser.add_argument('-u','--urls', help='Urls to download', nargs='+', required=False)
    parser.add_argument('-x','--exclude', help='Specifies what shows not to download [Ex: -x ova,special will make it so no ova or special episodes are downloaded]', nargs='+', required=False)
    parser.add_argument('-l','--lookup', help='Search', type=str, required=False)
    parser.add_argument('-t','--threads', help='Threads to use', type=int, default=1, required=False)
    parser.add_argument('-s','--season', help='Download a specific season', type=int, default=0, required=False)
    parser.add_argument('-r','--range', help='Ranges to download ex: 1, 1-10, 1-', type=str, default='all', required=False)
    parser.add_argument('-n','--dry-run', help='Dry run does everything except download', action='store_true', required=False)
    parser.add_argument('-d','--database', help='Show all downloaded shows', action='store_true', required=False)
    parser.add_argument('-ds','--database-show', help='Show all downloaded shows and episodes', action='store_true', required=False)
    parser.add_argument('-V','--version', help='Show version', action='store_true', required=False)
    args = parser.parse_args()
    return args

def main():
    args = args_parser()
    network_manager: Network = Network()
    scraper_manager: Scraper = Scraper() 
    database_manager: Database = Database()
    config: Config_Model = scraper_manager.configuration
    if (args.version):
        print(f'Version: {config.version}')
        return
    if (args.database):
        shows = database_manager.get_all_downloads(f'{config.config_directory}/db.json')
        for show in shows.keys():
            print(show)
        return
    if (args.database_show):
        shows = database_manager.get_all_downloads(f'{config.config_directory}/db.json')
        for show in shows.keys():
            print(show)
            for episode in shows[show]:
                info = scraper_manager.info_extractor(episode)
                print(f'    Downloaded: {info[1]} {info[2]}')
        return
    if (args.lookup):
        results = list(set(scraper_manager.search(args.lookup)))
        if (not results):
            print('Nothing found')
            return
        results.sort()
        title = 'Please choose (press SPACE to mark, ENTER to continue): '
        options = [result.replace('/anime/','').replace('-', ' ') for result in results]
        selected = pick.pick(options, title, multiselect=True, min_selection_count=0)
        if (args.urls is None):
            args.urls = []
        for item in selected:
            args.urls.append(f"https://www.wcostream.net/anime/{item[0].replace(' ', '-')}")
    for url in args.urls:
        if ('/anime/' in url):
            results =[]
            episodes_urls = scraper_manager.get_episodes(url)
            episodes_urls.reverse()
            database_manager.add_anime_to_database(scraper_manager.info_extractor(episodes_urls[0])[0], f'{config.config_directory}/db.json')
            if (args.season !=  0):
                episodes_urls = [url for url in episodes_urls if scraper_manager.info_extractor(url)[1] == f'Season {args.season}']
            if (args.range != 'all'):
                if ('-' in args.range):
                    start, end = int(args.range.split('-')[0]), int(args.range.split('-')[1]) if args.range.split('-')[1] != '' else 999_999_999
                    episodes_urls = [value for index, value in enumerate(episodes_urls) if index+1 >= start and index+1 <= end] 
                else:
                    start, end = int(args.range), int(args.range)
                    episodes_urls = [value for index, value in enumerate(episodes_urls) if index+1 >= start and index+1 <= end] 
            if (args.exclude):
                episodes_urls = [episode_url for episode_url in episodes_urls if not [exclude for exclude in args.exclude if exclude in episode_url]]
            if (args.threads > 1):
                if (not args.dry_run): 
                    with concurrent.futures.ThreadPoolExecutor(max_workers=args.threads) as exe:
                        results = exe.map(download_episode, episodes_urls, [network_manager]*len(episodes_urls),[scraper_manager]*len(episodes_urls),[config]*len(episodes_urls))
                        for index,result in enumerate(results):
                            if (result):
                                database_manager.add_episode_to_database(scraper_manager.info_extractor(episodes_urls[index])[0], episodes_urls[index], f'{config.config_directory}/db.json')
            else:
                if (not args.dry_run): 
                    for episode_url in episodes_urls:
                        if (download_episode(episode_url, network_manager, scraper_manager, config)):
                            database_manager.add_episode_to_database(scraper_manager.info_extractor(episode_url)[0], episode_url, f'{config.config_directory}/db.json')
        else:
            database_manager.add_anime_to_database(scraper_manager.info_extractor(url)[0], f'{config.config_directory}/db.json')
            if (args.exclude and [exclude for exclude in args.exclude if exclude in url]):
                print(f'Excluded: {url}')
                return
            if (not args.dry_run): 
                if (download_episode(url, network_manager, scraper_manager, config)):
                    database_manager.add_episode_to_database(scraper_manager.info_extractor(url)[0], url, f'{config.config_directory}/db.json')

if __name__ == '__main__':
    main()
