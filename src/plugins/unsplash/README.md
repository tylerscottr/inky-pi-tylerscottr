# Load image from Unsplash

Search and load an image from Unsplash.com

Requires a Free Unsplash API KEY from https://unsplash.com/developers 

- Register an account and get your API KEY. 
- Go to https://unsplash.com/oauth/applications 
- Create an app and open it
- Your API KEY is listed as `Access Key`
- Save your access key in `/.env` file as `UNSPLASH_ACCESS_KEY=`

Unsplash's search includes some loose matches. To skip them, check "Check that photos match the search with AI" and pick a model; it needs an OpenAI or Anthropic key and costs a fraction of a cent per photo.

Long press button A to hide the photo on screen for good and show a new one. Hidden photo IDs are kept in `src/plugins/unsplash/hidden_photos.json`. Change the button from the clock icon on the Playlists page.