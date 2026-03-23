import './globals.css';

export const metadata = {
  title: 'HireAI — Intelligent Hiring Copilot',
  description: 'AI-assisted candidate evaluation system with evidence-backed recommendations for smarter hiring decisions.',
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <head>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <link rel="icon" href="/favicon.ico" />
      </head>
      <body>{children}</body>
    </html>
  );
}
