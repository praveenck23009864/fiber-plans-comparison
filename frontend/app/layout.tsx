import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = { title: 'Broadband Compare — Find your connection', description: 'A clearer way to explore and compare broadband plans from JioFiber, ACT Fibernet and Hathway.' };
export default function Layout({ children }: { children: React.ReactNode }) { return <html lang="en"><body>{children}</body></html> }
