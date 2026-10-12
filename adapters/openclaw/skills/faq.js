const { readTab } = require('./sheet_reader');

const FAQ_TAB = 'faq';
const COLUMN_MAP = {
    question: ['Câu hỏi', 'question', 'Hỏi', 'Q'],
    answer: ['Trả lời', 'answer', 'Đáp', 'A'],
    keywords: ['Từ khóa', 'keywords', 'Tags']
};

async function queryFAQ(query) {
    if (!query) {
        console.log("Vui lòng cung cấp từ khóa để tìm kiếm FAQ.");
        return;
    }

    try {
        const faqs = await readTab(FAQ_TAB, COLUMN_MAP, 10); // Cache 10 mins

        if (!faqs || faqs.length === 0) {
            console.log("Chưa có dữ liệu FAQ.");
            return;
        }

        const q = query.toLowerCase();

        // Simple search logic based on keywords and question
        const results = faqs.filter(faq => {
            const questionText = (faq.question || '').toLowerCase();
            const keywordText = (faq.keywords || '').toLowerCase();
            return questionText.includes(q) || keywordText.includes(q);
        });

        if (results.length > 0) {
            console.log(`[Kết quả FAQ cho "${query}"]:`);
            results.forEach((r, i) => {
                console.log(`\n${i+1}. ${r.question}\n=> ${r.answer}`);
            });
        } else {
            console.log(`Không tìm thấy câu trả lời FAQ nào khớp với "${query}". Bạn có thể đổi từ khóa khác.`);
        }
    } catch (error) {
        console.error(`[Lỗi FAQ]: ${error.message}`);
    }
}

// Nếu gọi qua CLI: node faq.js "từ khóa"
if (require.main === module) {
    const query = process.argv.slice(2).join(' ');
    queryFAQ(query);
}

module.exports = { queryFAQ };
