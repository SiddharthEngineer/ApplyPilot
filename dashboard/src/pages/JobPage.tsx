import { useParams } from 'react-router-dom';

export default function JobPage() {
  const { key } = useParams();
  return <h1>Job {key}</h1>;
}
