import Head from "next/head";
import Layout from "@/features/ui/layout";
import Footer from "@/features/ui/footer";
import { ProjectName } from "@/common/constants";

export default function New() {
  return (
    <>
      <Head>
        <title>{ProjectName} New Cards</title>
        <meta name="description" content="TODO" /> {/* TODO */}
      </Head>
      <Layout>
        <h1>Test</h1>
        <p>Should be my new page</p>
        <Footer />
      </Layout>
    </>
  );
}
