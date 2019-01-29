#!/usr/local/bin/python3.5
from git import *
import argparse
import os
import asyncio
import aiohttp

user = ''
token = ''
with open('auth.txt', 'r') as file:
	user, token = file.read().split(':')

repositories = []

def init():
	print('Initialising')

	with open('repositories.txt', 'r') as file:
		repositories = file.read().splitlines()

		for repo in repositories:

			full_name = repo.split('/')
			directory = 'repositories/{}_{}'.format(full_name[0], full_name[1])

			if not os.path.exists(directory):
				os.makedirs(directory)

class Crawler:
	def __init__(self, repository, max_concurrency=100):
		self.page = 'https://api.github.com/repos/{}/pulls?state=closed&page=1&per_page=100'.format(repository)
		self.repository = repository
		self.session = aiohttp.ClientSession(auth=aiohttp.BasicAuth(user, token))
		self.bounded_sempahore = asyncio.BoundedSemaphore(max_concurrency)
		self.accepted_prs = []
		self.rejected_prs = []

	async def _http_request(self, url):
		print('Fetching: {}'.format(url))
		async with self.bounded_sempahore:
			try:
				async with self.session.get(url, timeout=30) as response:
					return await response.json()
					
			except Exception as e:
				print('Exception: {}'.format(e))

	async def verify_presence_of_review_comments(self, pr, url):
		print('Verifying presence of review comments of #{}.'.format(pr))

		response = await self._http_request(url)
		
		if(len(response) == 0):
			print("PR {} -- LEN {}".format(pr, len(response)))
			return False
		print("True")
		return True
	
	async def verify_acceptance(self, pr):
		print('Verifying acceptance of pull request #{}'.format(pr))

		response = await self._http_request('https://api.github.com/repos/{}/pulls/{}'.format(self.repository, pr))

		status = await self.verify_presence_of_review_comments(pr, response["review_comments_url"])
		print("Status {} -- Review {}".format(status, response["review_comments_url"]))

		if(response["merged_at"] != None):
			if(status == True):
				return True
			else:
				return False
		else:
			return None

	async def filter_by_presence_of_changed_files(self, pr):
		print('Verifying presence of changed files in pull request: {}'.format(pr))

		response = await self._http_request('https://api.github.com/repos/{}/pulls/{}'.format(self.repository, pr))
		
		if response["changed_files"] > 0:
			return True

		return False

	async def extract_review_comments(self, pr):
		status = await self.filter_by_presence_of_changed_files(pr)

		if(status):
			if(await self.verify_acceptance(pr) == True):
				return True, pr
			else:
				return False, pr

	async def extract_multi_async(self):
		futures = []

		response = await self._http_request(self.page)
		# Ta certo aki
		for pr in response:
			futures.append(self.extract_review_comments(str(pr["number"])))

		for future in asyncio.as_completed(futures):
			
			try:
				status, pr =  await future

				if (status == True):
					self.accepted_prs.append(pr)
				elif (status == False):
					self.rejected_prs.append(pr)
			except Exception as e:
				print('Encountered exception: {}'.format(e))

	async def crawl_async(self):

		while True:
			async with self.bounded_sempahore:
				try:
					await self.extract_multi_async()

					async with self.session.get(self.page, timeout=30) as response:
						self.page = response.links['next']['url']
						
				except Exception as e:
					print('Exception: {}'.format(e))
					break

		print('LEN: {} Accepted PRs: {}'.format(len(self.accepted_prs), self.accepted_prs))
		print('LEN: {} Rejected PRs: {}'.format(len(self.rejected_prs), self.rejected_prs))

if __name__ == '__main__':
	parser = argparse.ArgumentParser()
	parser.add_argument("--auth", help="GitHub's API authentication token.\n(Format: <username>:<api token>)")
	args = parser.parse_args()

	if args.auth:
		with open('config/auth.txt', 'w') as file:
			file.write(args.auth)

	init()

	crawler = Crawler('rails/rails')
	future = asyncio.Task(crawler.crawl_async())
	loop = asyncio.get_event_loop()
	loop.run_until_complete(future)
	loop.close()
