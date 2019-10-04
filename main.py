#!/usr/local/bin/python3.5
import argparse
import os
from SentiCR.SentiCR import  SentiCR
import pickle
import asyncio
import aiohttp
import time
import datetime
import numpy as np
import math
import time

import pymongo

users = []
tokens = []
user = ''
token = ''

mongo_client = pymongo.MongoClient("mongodb://localhost:27017/")
database = mongo_client["reposadb"]
pull_requests_collection = database["pull_requests"]
repositories_collection = database["repositories"]

with open('auth.txt', 'r') as file:
	for line in file.readlines():
		user, token = line.split(':')
		users.append(user.replace('\n', ''))
		tokens.append(token.replace('\n', ''))
		
repositories = []

def init():
	print('Initialising')


	with open('repositories.txt', 'r') as file:
		repositories = file.read().splitlines()

	for repository in repositories:
		owner, name = repository.split("/")
		query = { "_id" : "{}/{}".format(owner, name) }
		documento = list(repositories_collection.find(query))
		
		if(len(documento) == 0):
			repositories_collection.insert_one({
												"_id" : "{}/{}".format(owner, name),
												"owner": owner,
												"name" : name,
												'open_pull_requests' : [],
												'closed_pull_requests' : []
											})
def classify(sentences):

    saved_SentiCR_model = 'classifier_models/SentiCR_model.sav'
    
    if(os.path.exists(saved_SentiCR_model)):
      sentiment_analyzer = pickle.load(open(saved_SentiCR_model, 'rb'))
      print('Loaded SentiCR model')
    else:
      sentiment_analyzer = SentiCR.SentiCR()
      pickle.dump(sentiment_analyzer, open(saved_SentiCR_model, 'wb'))
      print('Saved model to file')

    for sent in sentences:
        score = sentiment_analyzer.get_sentiment_polarity(sent)
        print(sent+"\n Score: "+str(score))

class Crawler:
	def __init__(self, repository, max_concurrency=10):
		self.key = 0
		self.user = users[self.key]
		self.token = tokens[self.key]
		self.page = 'https://api.github.com/repos/{}/pulls?state=closed&page=1&per_page=100&client_id={}&client_secret={}'.format(repository, self.user, self.token)
		self.repository = repository
		self.bounded_sempahore = asyncio.BoundedSemaphore(max_concurrency)
		self.accepted_prs = []
		self.rejected_prs = []
		self.sleep_delay = 3

		# Cannot connect to host api.github.com:443 ssl:None [Connect call failed ('140.82.113.5', 443)]
		##self.conn = aiohttp.TCPConnector(family=socket.AF_INET, verify_ssl=False)
		
		# UTC-3 + 5 minutes
		self.auth_lock_timer = datetime.datetime.utcnow() - datetime.timedelta(hours=3) + datetime.timedelta(minutes=5)

	def roll_auth(self):
		curr_time = datetime.datetime.utcnow() - datetime.timedelta(hours=3)

		# If 5 miutes have been gone by from the last time we rolled the HTTP authentication
		if(curr_time > self.auth_lock_timer):
			# Set the lock to 5 minutes from now
			self.auth_lock_timer = curr_time + datetime.timedelta(minutes=5)
			print("Rolling Authentication")
			if self.key < len(users):
				self.key += 1
			else:
				self.key = 0

			self.user = users[self.key]
			self.token = tokens[self.key]
		else:
			print("Not rolling authentication")

	async def _http_request(self, url):
		await asyncio.sleep(1.5)

		print('Fetching: {}'.format(url))

		async with self.bounded_sempahore:
			try:
				async with aiohttp.ClientSession(connector = aiohttp.TCPConnector(ssl=False)) as client:
					async with client.get(url, timeout=30) as response:
						resp =  await response.json()
						if("200" in response.headers["status"]):
							if(int(response.headers["X-RateLimit-Remaining"]) <= 100):
								self.roll_auth()

							return resp
						
						if(response.status == 403):
							if("You have triggered an abuse detection mechanism" in resp["message"]):
								print("ABUSE")
							# If Rate Limit has exceeded, wait for the reset cooldown and try again.
							elif('API rate limit exceeded' in resp["message"]):
								print("Exceeded for {}. URL: {}".format(self.user, url))
								reset_time = float(response.headers['X-RateLimit-Reset'])

								reset_time_format = datetime.datetime.fromtimestamp(reset_time)
								print("Reset Time: {}".format(reset_time_format))
								current_time = datetime.datetime.now()
								print("Curr Time: {}".format(current_time))
								
								#Change credentials and update to new url
								url = url.split('?client_id')[0]
								url += '?client_id={}&client_secret={}'.format(self.user, self.token)
								self.roll_auth()

								print("NEW URL: {}".format(url))
								return await self._http_request(url)
						
			except Exception as e:
				print('HTTP Exception: {}'.format(e))

	async def verify_presence_of_review_comments(self, pr):
		print('Verifying presence of review comments of #{}.'.format(pr))

		response = await self._http_request('https://api.github.com/repos/{}/pulls/{}/comments?client_id={}&client_secret={}'.format(self.repository, pr, self.user, self.token))
		
		print("PR: {}\nLEN: {}\n".format(pr, len(response)))
		if(len(response) == 0):
			return False
		else:
			return True
	
	async def verify_acceptance(self, pr):
		print('Verifying acceptance of pull request #{}'.format(pr))

		response = await self._http_request('https://api.github.com/repos/{}/pulls/{}?client_id={}&client_secret={}'.format(self.repository, pr, self.user, self.token))

		if(response["merged_at"] != None):
				return True
		else:
				return False

	async def filter_by_presence_of_changed_files(self, pr):
		print('Verifying presence of changed files in pull request: {}'.format(pr))

		response = await self._http_request('https://api.github.com/repos/{}/pulls/{}?client_id={}&client_secret={}'.format(self.repository, pr, self.user, self.token))

		if response["changed_files"] > 0:
			return True

		return False

	async def filter_pull_requests(self, pr):
		status = await self.filter_by_presence_of_changed_files(pr)

		if(status):
			if(await self.verify_presence_of_review_comments(pr) == True):
				return await self.verify_acceptance(pr), pr

	async def extract_multi_async(self):
		futures = []

		response = await self._http_request(self.page)

		for pr in response:
			futures.append(self.filter_pull_requests(str(pr["number"])))

		print(len(futures))
		for future in asyncio.as_completed(futures):
			try:
				status, pr = await future
				print("Status: {}\nPR: {}".format(status, pr))

				if (status == True):
					self.accepted_prs.append(pr)
				elif (status == False):
					self.rejected_prs.append(pr)
			except Exception as e:
					print('Extract Multi Exception: {}'.format(e))

	# Backwards
	async def crawl_async(self):

		async with aiohttp.ClientSession() as client:
			async with client.get(self.page, timeout=30) as response:
				last_page_number = int(str(response.links['last']['url']).split("page=")[1].replace('&per_', '')) + 1
		
		
		for i in range(1, last_page_number):
			async with self.bounded_sempahore:
				try:
					self.page = 'https://api.github.com/repos/{}/pulls?state=closed&page={}&per_page=100&client_id={}&client_secret={}'.format(self.repository, i, self.user, self.token)

					await self.extract_multi_async()
						
				except Exception as e:
					print('Crawl for Exception: {}'.format(e))
					
		print('LEN: {} Accepted PRs: {}'.format(len(self.accepted_prs), self.accepted_prs))
		print('LEN: {} Rejected PRs: {}'.format(len(self.rejected_prs), self.rejected_prs))

	async def crawl_first_page(self):
		async with self.bounded_sempahore:
			try:
				await self.extract_multi_async()

				await self.extract_review_comments()
			except Exception as e:
				print('Crawl first page Exception: {}'.format(e))
		
		trackers = []
		print('LEN: {} Accepted PRs: {}'.format(len(self.accepted_prs), self.accepted_prs))
		print('LEN: {} Rejected PRs: {}'.format(len(self.rejected_prs), self.rejected_prs))

	async def get_review_comments_from_pull_request(self, pull_request):
		print('Fetching comments from pull request #' + pull_request)

		response = await self._http_request('https://api.github.com/repos/{}/issues/{}/comments?client_id={}&client_secret={}'.format(self.repository, pr, self.user, self.token))
		comments = []

		if response.ok:
			
			for comment in response.json():
				comments.append(comment['body'])

			print('Done.') 

			return comments
		else:
			return response.raise_for_status()

	async def extract_review_comments():
		for pr in self.accepted_prs:
			self.get_review_comments_from_pull_request(pr)

	###################

	async def get_pull_request_review_comments(self, pr):
		print('Fetching review comments from pull request #{}'.format(pr))
		response = await self._http_request('https://api.github.com/repos/{}/pulls/{}/comments?client_id={}&client_secret={}'.format(self.repository, pr, self.user, self.token))
				
		review_comments = []
		
		for review_comment in response:
			review_comment_object = {
				'_id' : review_comment['id'],
				'url' : review_comment['url'],
				'user' : {
					'login' : review_comment['user']['login'],
					'type' : review_comment['user']['type'],
					'site_admin' : review_comment['user']['site_admin']
				}, 
				'body' : review_comment['body'],
				'created_at' : review_comment['created_at'],
				'updated_at' : review_comment['updated_at'],
				'author_association' : review_comment['author_association'],
			}

			review_comments.append(review_comment_object)

		return review_comments

	async def get_pull_request_issue_comments(self, pr):
		print('Fetching issue comments from pull request #{}'.format(pr))
		response = await self._http_request('https://api.github.com/repos/{}/issues/{}/comments?client_id={}&client_secret={}'.format(self.repository, pr, self.user, self.token))
		
		issue_comments = []
		for issue_comment in response:
			issue_comment_object = {
				'_id' : issue_comment['id'],
				'url' : issue_comment['url'],
				'user' : {
					'login' : issue_comment['user']['login'],
					'type' : issue_comment['user']['type'],
					'site_admin' : issue_comment['user']['site_admin']
				}, 
				'body' : issue_comment['body'],
				'created_at' : issue_comment['created_at'],
				'updated_at' : issue_comment['updated_at'],
				'author_association' : issue_comment['author_association'],
			}

			issue_comments.append(issue_comment_object)
		return issue_comments

	async def extract_pull_request(self, pr):
		print('Fetching pull request #{}'.format(pr))
		response =  await self._http_request('https://api.github.com/repos/{}/pulls/{}?client_id={}&client_secret={}'.format(self.repository, pr, self.user, self.token))

		if response['changed_files'] > 0:
			issue_comments = await self.get_pull_request_issue_comments(pr)

			review_comments = await self.get_pull_request_review_comments(pr)

			requested_reviewers = []
			for reviewer in response['requested_reviewers']:
				reviewer_object = {
					'login' : reviewer['login'],
					'type' : reviewer['type'],
					'site_admin' : reviewer['site_admin']
				}

				requested_reviewers.append(reviewer_object)

			pull_request = {
				'_id' : response['number'],
				'title' : response['title'],
				'url' : response['url'],
				'state' : response['state'],
				'user' : {
					'login' : response['user']['login'],
					'type' : response['user']['type'],
					'site_admin' : response['user']['site_admin']
				},
				'body' : response['body'],
				'created_at' : response['created_at'],
				'updated_at' : response['updated_at'],
				'closed_at' : response['closed_at'],
				'merged_at' : response['merged_at'],
				'assigne' : response['assignee'],
				'assignes' : response['assignees'],
				'requested_reviewers' :requested_reviewers,
				'repository_id' : response['base']['repo']['full_name'],
				'review_comments' : review_comments,
				'issue_comments' : issue_comments,
				'author_association' : response['author_association'],
				'merged' : response['merged'],
				'mergeable' : response['mergeable'],
				'rebaseable' : response['rebaseable'],
				'mergeable_state' : response['mergeable_state'],
				'merged_by' : response['merged_by'],
				'comments_count' : response['comments'],
				'review_comments_count' : response['review_comments'],
				'maintainer_can_modify' : response['maintainer_can_modify'],
				'commits_count' : response['commits'],
				'additions' : response['additions'],
				'deletions' : response['deletions'],
				'changed_files' : response['changed_files']
			}

			return pull_request
		else:
			print('Cancel Task.')
			asyncio.Tasks.current_task().cancel()

	# Queries Github repository and repository_document for changes.
	# returns futures job list
	async def query_changes(self):
		repository_query = {"_id" : self.repository}

		repository_document = list(repositories_collection.find(repository_query))
		document_open_pull_requests = repository_document[0]["open_pull_requests"]
		document_closed_pull_requests = repository_document[0]["closed_pull_requests"]
		
		open_pull_requests = []
		closed_pull_requests = []
		futures = []

		# state = open
		self.page = 'https://api.github.com/repos/{}/pulls?state=open&page=1&per_page=100&client_id={}&client_secret={}'.format(self.repository, self.user, self.token)
		async with aiohttp.ClientSession() as client:
			async with client.get(self.page, timeout=30) as response:
				if(len(response.links) == 0):
					last_page_number = 1
				else:					
					last_page_number = int(str(response.links['last']['url']).split("page=")[1].replace('&per_', '')) + 1

		# Forwards
		async with self.bounded_sempahore:
			try:
				# Crawl from last page to page #1 searching for modifications.
				for i in range(last_page_number, 0, -1):
					self.page = 'https://api.github.com/repos/{}/pulls?state=open&page={}&per_page=100&client_id={}&client_secret={}'.format(self.repository, i, self.user, self.token)
					response = await self._http_request(self.page)

					for pr in response:
						open_pull_requests.append(str(pr["number"]))
			except Exception as e:
				print('Crawl first page Exception: {}'.format(e))

		for pr in open_pull_requests:
			pr = int(pr)
			# Inserts Pull Request if not present.
			if(pr not in document_open_pull_requests):
				repositories_collection.update_one({'_id' : self.repository}, { '$push' : {'open_pull_requests' : pr} })
				if(pr in document_closed_pull_requests):
					repositories_collection.update_one({'_id' : self.repository}, { '$pull' : {'closed_pull_requests' : pr} })



		# state = closed
		self.page = 'https://api.github.com/repos/{}/pulls?state=closed&page=1&per_page=100&client_id={}&client_secret={}'.format(self.repository, self.user, self.token)
		async with aiohttp.ClientSession() as client:
			async with client.get(self.page, timeout=30) as response:
				if(len(response.links) == 0):
					last_page_number = 1
				else:					
					last_page_number = int(str(response.links['last']['url']).split("page=")[1].replace('&per_', '')) + 1

		# Forwards
		async with self.bounded_sempahore:
			try:
				# Crawl from last page to page #1 searching for modifications.
				for i in range(last_page_number, 0, -1):
					self.page = 'https://api.github.com/repos/{}/pulls?state=closed&page={}&per_page=100&client_id={}&client_secret={}'.format(self.repository, i, self.user, self.token)
					response = await self._http_request(self.page)

					for pr in response:
						closed_pull_requests.append(str(pr["number"]))
			except Exception as e:
				print('Crawl first page Exception: {}'.format(e))

		for pr in closed_pull_requests:
			pr = int(pr)
			# Inserts
			if(pr not in document_closed_pull_requests):
				#repositories_collection.update_one({'_id' : self.repository}, { '$push' : {'closed_pull_requests' : pr} })

				futures.append(self.extract_pull_request(str(pr)))

				if(pr in document_open_pull_requests):
					repositories_collection.update_one({'_id' : self.repository}, { '$pull' : {'open_pull_requests' : pr} })

		return futures		

	async def update_repository(self):
		pull_requests = []
		inserted_prs = []

		futures_list = await self.query_changes()

		futures_matrix = []
		start = 0
		length = math.ceil((len(futures_list)) / 10)
		for i in range(length):
			end = start + 20
			futures_matrix.append(futures_list[start:end])
			start += 20

		for i in range(len(futures_matrix)):
			futures = futures_matrix[i]
			for future in asyncio.as_completed(futures):
				try:
					pull_request = await future
					pull_requests.append(pull_request)
					inserted_prs.append(pull_request['_id'])
					print('inserting to db')
					pull_requests_collection.insert_one(pull_request)
					repositories_collection.update_one({'_id' : self.repository}, { '$push' : {'closed_pull_requests' : pull_request['_id']} })

				except Exception as e:
					print('Extract Multi Exception: {}'.format(e))

if __name__ == '__main__':
	parser = argparse.ArgumentParser()
	parser.add_argument("--auth", help="GitHub's API authentication token.\n(Format: <username>:<api token>)")
	parser.add_argument("--rebase", help="Rebase.\n(Format: <username>:<api token>)")
	parser.add_argument('-u', '--update', action='store', dest='update', help='The repository to be updated.')
	args = parser.parse_args()

	start_time = time.time()

	init()

	if args.auth:
		with open('config/auth.txt', 'w') as file:
			file.write(args.auth)
	elif args.rebase:
		print("TODO")
	elif args.update:
		try:
			crawler = Crawler(args.update)
			#future = asyncio.Task(crawler.crawl_async())
			loop = asyncio.get_event_loop()
			#loop.run_until_complete(crawler.crawl_async())
			loop.run_until_complete(crawler.update_repository())
		except: 
			pass
		finally:
			loop.close()

	elapsed_time = time.time() - start_time
	formatted_elapsed_time = time.strftime("%H:%M:%S", time.gmtime(elapsed_time))

	print(formatted_elapsed_time)


"""	try:
		crawler = Crawler('vuejs/vue')
		#future = asyncio.Task(crawler.crawl_async())
		loop = asyncio.get_event_loop()
		#loop.run_until_complete(crawler.crawl_async())
		loop.run_until_complete(crawler.crawl_first_page())
	except: 
		pass
	finally:
		loop.close()
"""