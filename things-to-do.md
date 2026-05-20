- Clean up README.md
- Allow for full file viewer in gitnexus graph
- FIX UI:
	- Make the ui look less like ai streamlit slop
	- On v0:
		-
- Stripe: consider including payments at any time - stacking analyses
- Consider adding more functionality
- Post on LinkedIn
	- Pictures from presentation
	- Make a gif scrolling down the analysis page

Deployment stuff:
- Frontend (Next.js): Vercel
- Backend (FastAPI): Render
- Database: Supabase
- Vector DB: Pinecone=
- AI models: OpenRouter
- Payments: Stripe
- GitHub Oauth: GitHub
- Voyage embeddings: Voyage AI

Deployment checklist before going live:                                       
  1. Push this to GitHub                                    
  2. On Render: create a new Web Service pointing at your repo — render.yaml    
  will be detected automatically. Fill in all sync: false vars in the dashboard.
  3. On Vercel: import the frontend/ directory. Set NEXT_PUBLIC_API_URL to your 
  Render backend URL (e.g. https://codelens-api.onrender.com).                 
  4. Update your GitHub OAuth App's "Authorization callback URL" to             
  https://codelens-api.onrender.com/api/auth/callback.             
  5. Set up UptimeRobot to hit https://codelens-api.onrender.com/api/health     
  every 10 minutes to prevent Render free-tier cold starts. 